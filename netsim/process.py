"""Child processes attached to a pseudo-terminal.

A pty makes programs line-buffer their output (so logs are live) and gives
interactive shells job control. Each child gets its own session so the whole
process group can be signalled.
"""

from __future__ import annotations

import asyncio
import fcntl
import os
import re
import signal
import struct
import termios
from typing import Callable

ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07|\x1b[()][A-Za-z0-9]")


class PtyProcess:
    def __init__(self, argv: list[str], env: dict[str, str], cwd: str | None = None,
                 on_output: Callable[[bytes], None] | None = None,
                 on_exit: Callable[[int], None] | None = None):
        self.argv, self.env, self.cwd = argv, env, cwd
        self.on_output = on_output or (lambda b: None)
        self.on_exit = on_exit or (lambda c: None)
        self.proc: asyncio.subprocess.Process | None = None
        self.master: int | None = None
        self.returncode: int | None = None
        self._waiter: asyncio.Task | None = None

    @property
    def pid(self) -> int | None:
        return self.proc.pid if self.proc else None

    async def start(self, cols: int = 120, rows: int = 32) -> None:
        master, slave = os.openpty()
        self._set_size(slave, cols, rows)
        try:
            # setsid -c: new session with the pty as controlling terminal
            self.proc = await asyncio.create_subprocess_exec(
                "setsid", "-c", *self.argv, stdin=slave, stdout=slave, stderr=slave,
                env=self.env, cwd=self.cwd)
        finally:
            os.close(slave)
        self.master = master
        os.set_blocking(master, False)
        asyncio.get_running_loop().add_reader(master, self._read)
        self._waiter = asyncio.create_task(self._wait())

    def _read(self) -> None:
        try:
            data = os.read(self.master, 65536)
        except BlockingIOError:
            return
        except OSError:
            data = b""
        if not data:
            asyncio.get_running_loop().remove_reader(self.master)
            return
        self.on_output(data)

    async def _wait(self) -> None:
        code = await self.proc.wait()
        await asyncio.sleep(0.05)  # let the reader drain trailing output
        if self.master is not None:
            asyncio.get_running_loop().remove_reader(self.master)
            try:
                while data := os.read(self.master, 65536):
                    self.on_output(data)
            except OSError:
                pass
            os.close(self.master)
            self.master = None
        self.returncode = code
        self.on_exit(code)

    def write(self, data: bytes) -> None:
        if self.master is not None:
            try:
                os.write(self.master, data)
            except OSError:
                pass

    def resize(self, cols: int, rows: int) -> None:
        if self.master is not None:
            self._set_size(self.master, cols, rows)

    @staticmethod
    def _set_size(fd: int, cols: int, rows: int) -> None:
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))

    def signal(self, sig: int) -> None:
        if self.proc and self.returncode is None:
            try:
                os.killpg(self.proc.pid, sig)
            except ProcessLookupError:
                pass

    async def stop(self, timeout: float = 3.0) -> None:
        if not self.proc or self.returncode is not None:
            return
        self.signal(signal.SIGTERM)
        try:
            await asyncio.wait_for(asyncio.shield(self._waiter), timeout)
        except asyncio.TimeoutError:
            self.signal(signal.SIGKILL)
            await self._waiter


class LineSplitter:
    """Turns pty byte chunks into clean text lines."""

    def __init__(self, emit: Callable[[str], None]):
        self.emit = emit
        self.buf = ""

    def feed(self, data: bytes) -> None:
        self.buf += data.decode(errors="replace")
        *lines, self.buf = self.buf.split("\n")
        for line in lines:
            self.emit(ANSI_RE.sub("", line).rstrip("\r"))
        if len(self.buf) > 8192:
            self.emit(ANSI_RE.sub("", self.buf))
            self.buf = ""

    def flush(self) -> None:
        if self.buf:
            self.emit(ANSI_RE.sub("", self.buf).rstrip("\r"))
            self.buf = ""


async def stream_lines(argv: list[str], on_line: Callable[[str], None], env: dict | None = None
                       ) -> asyncio.subprocess.Process:
    """Start a non-interactive process and feed each stdout/stderr line to on_line."""
    proc = await asyncio.create_subprocess_exec(
        *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        stdin=asyncio.subprocess.DEVNULL, env=env, start_new_session=True)

    async def pump():
        async for raw in proc.stdout:
            on_line(raw.decode(errors="replace").rstrip("\n"))
    proc.pump = asyncio.create_task(pump())  # type: ignore[attr-defined]
    return proc


async def kill_tree(proc: asyncio.subprocess.Process, timeout: float = 2.0) -> None:
    if proc.returncode is not None:
        return
    for sig in (signal.SIGINT, signal.SIGKILL):
        try:
            os.killpg(proc.pid, sig)
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(proc.wait(), timeout)
            return
        except asyncio.TimeoutError:
            continue
