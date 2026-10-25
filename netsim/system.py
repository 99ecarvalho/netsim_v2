# SPDX-License-Identifier: LGPL-3.0-or-later
# Copyright (C) 2026 Eduardo Correia
"""Thin async wrappers over ip/tc/nft/iptables plus namespace-aware sockets."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shlex
import socket
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable

log = logging.getLogger("netsim.system")

_ROOT_NS_FD: int | None = None
_ns_lock = threading.Lock()
TC_ENV: dict[str, str] = {}


class CommandError(RuntimeError):
    def __init__(self, argv: list[str], code: int, stderr: str):
        self.argv, self.code, self.stderr = argv, code, stderr.strip()
        super().__init__(f"`{shlex.join(argv)}` failed ({code}): {self.stderr}")


@dataclass
class CommandRecord:
    t: float
    argv: list[str]
    code: int
    stderr: str
    input: str | None = None

    def to_json(self):
        return {"t": self.t, "cmd": shlex.join(self.argv), "code": self.code,
                "stderr": self.stderr, "input": self.input}


class Runner:
    """Executes commands and remembers them, so the UI can show what was done."""

    def __init__(self, history: int = 3000, on_record: Callable[[CommandRecord], None] | None = None):
        self.history: deque[CommandRecord] = deque(maxlen=history)
        self.on_record = on_record

    async def run(self, argv: list[str], *, check: bool = True, input: str | None = None,
                  env: dict[str, str] | None = None, quiet: bool = False) -> str:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE if input is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            env={**os.environ, **(env or {})},
        )
        out, err = await proc.communicate(input.encode() if input is not None else None)
        stderr = err.decode(errors="replace")
        if not quiet:
            rec = CommandRecord(time.time(), argv, proc.returncode, stderr.strip(), input)
            self.history.append(rec)
            if self.on_record:
                self.on_record(rec)
        if check and proc.returncode != 0:
            raise CommandError(argv, proc.returncode, stderr)
        return out.decode(errors="replace")

    async def ip(self, ns: str | None, *args: str, **kw) -> str:
        return await self.run(["ip", *(["-n", ns] if ns else []), *args], **kw)

    async def ip_batch(self, ns: str | None, lines: list[str], **kw) -> str:
        if not lines:
            return ""
        return await self.run(["ip", *(["-n", ns] if ns else []), "-batch", "-"], input="\n".join(lines) + "\n", **kw)

    async def ip_json(self, ns: str | None, *args: str) -> list:
        out = await self.ip(ns, "-j", *args, quiet=True)
        return json.loads(out) if out.strip() else []

    async def tc(self, ns: str | None, *args: str, **kw) -> str:
        return await self.run(["tc", *(["-n", ns] if ns else []), *args], env=TC_ENV, **kw)

    async def nsexec(self, ns: str | None, *argv: str, **kw) -> str:
        return await self.run([*nsexec_prefix(ns), *argv], **kw)


def nsexec_prefix(ns: str | None) -> list[str]:
    return ["ip", "netns", "exec", ns] if ns else []


def drop_privs_prefix(uid: int, gid: int) -> list[str]:
    if uid == 0:
        return []
    return ["setpriv", f"--reuid={uid}", f"--regid={gid}", "--init-groups", "--"]


async def list_netns() -> list[str]:
    proc = await asyncio.create_subprocess_exec("ip", "-j", "netns", "list", stdout=asyncio.subprocess.PIPE,
                                                stderr=asyncio.subprocess.DEVNULL)
    out, _ = await proc.communicate()
    try:
        return [e["name"] for e in json.loads(out or b"[]")]
    except (ValueError, KeyError):
        return []


def socket_in_ns(ns: str | None, family=socket.AF_INET, type_=socket.SOCK_STREAM, proto: int = 0) -> socket.socket:
    """Create a socket living in network namespace `ns` (None = root).

    setns() only affects the calling thread and a socket stays in the namespace
    it was created in, so we hop in, create it, and hop back.
    """
    global _ROOT_NS_FD
    if ns is None:
        s = socket.socket(family, type_, proto)
    else:
        with _ns_lock:
            if _ROOT_NS_FD is None:
                _ROOT_NS_FD = os.open("/proc/thread-self/ns/net", os.O_RDONLY)
            fd = os.open(f"/run/netns/{ns}", os.O_RDONLY)
            try:
                os.setns(fd, os.CLONE_NEWNET)
                try:
                    s = socket.socket(family, type_, proto)
                finally:
                    os.setns(_ROOT_NS_FD, os.CLONE_NEWNET)
            finally:
                os.close(fd)
    s.setblocking(False)
    return s


def init_root_ns_fd() -> None:
    """Remember the daemon's own namespace; call once from the main thread."""
    global _ROOT_NS_FD
    if _ROOT_NS_FD is None:
        _ROOT_NS_FD = os.open("/proc/thread-self/ns/net", os.O_RDONLY)
