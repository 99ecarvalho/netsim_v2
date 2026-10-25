# SPDX-License-Identifier: LGPL-3.0-or-later
# Copyright (C) 2026 Eduardo Correia
"""Packet capture on a lab interface.

We capture ourselves with an AF_PACKET socket opened inside the namespace and
write a standard pcap file. For human-readable summaries the same pcap stream is
piped into `tcpdump -r -`, which exits cleanly on EOF. (A live tcpdump cannot be
stopped by the daemon on systems where AppArmor confines it: even root's
signals are refused.)
"""

from __future__ import annotations

import asyncio
import socket
import struct
import time
from pathlib import Path
from typing import Callable

from netsim.system import socket_in_ns

ETH_P_ALL = 0x0003
SNAPLEN = 262144
# linktype 113 = Linux "cooked" (SLL): keeps whether a packet was received or sent,
# so captures on routers show direction correctly (Wireshark shows it too)
PCAP_HEADER = struct.pack("<IHHiIII", 0xA1B2C3D4, 2, 4, 0, 0, SNAPLEN, 113)
ARPHRD_ETHER = 1
MAX_BYTES = 200 * 1024 * 1024  # captures live in /run (memory): stop instead of filling it


class PacketCapture:
    def __init__(self, ns: str | None, iface: str, path: Path, display_filter: str,
                 on_line: Callable[[str], None], on_limit: Callable[[], None] | None = None,
                 max_bytes: int = MAX_BYTES):
        self.ns, self.iface, self.path = ns, iface, path
        self.on_limit = on_limit or (lambda: None)
        self.max_bytes = max_bytes
        self.truncated = False
        self.display_filter = display_filter
        self.on_line = on_line
        self.count = 0
        self.bytes = 0
        self.running = False
        self._sock: socket.socket | None = None
        self._file = None
        self._dissector: asyncio.subprocess.Process | None = None
        self._pump: asyncio.Task | None = None

    async def start(self) -> None:
        loop = asyncio.get_running_loop()
        self._dissector = await asyncio.create_subprocess_exec(
            "tcpdump", "-r", "-", "-nn", "-tt", "-e", "-l", *self.display_filter.split(),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        self._dissector.stdin.write(PCAP_HEADER)
        await asyncio.sleep(0.2)
        if self._dissector.returncode is not None:
            err = (await self._dissector.stderr.read()).decode(errors="replace").strip()
            raise ValueError(f"invalid filter: {err.splitlines()[-1] if err else self.display_filter}")
        self._sock = socket_in_ns(self.ns, socket.AF_PACKET, socket.SOCK_RAW, socket.htons(ETH_P_ALL))
        self._sock.bind((self.iface, 0))
        self._file = open(self.path, "wb", buffering=0)
        self._file.write(PCAP_HEADER)
        self._pump = asyncio.create_task(self._read_text())
        loop.add_reader(self._sock, self._rx)
        self.running = True

    def _rx(self) -> None:
        while True:
            try:
                frame, addr = self._sock.recvfrom(SNAPLEN)
            except (BlockingIOError, InterruptedError):
                return
            except OSError:
                self._detach()
                return
            if len(frame) < 14:
                continue
            ts = time.time_ns()
            # SLL header: packet type, ARPHRD, address length, source MAC (8 bytes), ethertype
            data = struct.pack(">HHH8s", addr[2], ARPHRD_ETHER, 6, frame[6:12]) + frame[12:14] + frame[14:]
            rec = struct.pack("<IIII", ts // 1_000_000_000, ts % 1_000_000_000 // 1000, len(data), len(data)) + data
            if self.bytes + len(rec) > self.max_bytes:
                self.truncated = True
                self._detach()
                self.on_limit()
                return
            self.count += 1
            self.bytes += len(rec)
            self._file.write(rec)
            if self._dissector and self._dissector.returncode is None:
                try:
                    self._dissector.stdin.write(rec)
                except (BrokenPipeError, ConnectionResetError, RuntimeError):
                    pass

    async def _read_text(self) -> None:
        async for raw in self._dissector.stdout:
            self.on_line(raw.decode(errors="replace").rstrip("\n"))

    def _detach(self) -> None:
        if self._sock is not None:
            asyncio.get_running_loop().remove_reader(self._sock)
            self._sock.close()
            self._sock = None

    async def stop(self) -> None:
        if not self.running:
            return
        self.running = False
        self._detach()
        if self._file:
            self._file.close()
        if self._dissector and self._dissector.returncode is None:
            try:
                self._dissector.stdin.close()
                await asyncio.wait_for(self._dissector.wait(), 3)
            except (asyncio.TimeoutError, BrokenPipeError):
                pass
        if self._pump:
            try:
                await asyncio.wait_for(self._pump, 1)
            except asyncio.TimeoutError:
                self._pump.cancel()
