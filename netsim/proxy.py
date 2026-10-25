"""TCP/UDP relays that push one specific conversation through a lab.

    app --> [listen, listen_ns] ==daemon==> entry node socket ~~shaped~~>
        exit node --> [target, target_ns]

When the target lives inside the exit node the entry socket connects straight
to it; otherwise an internal relay listens in the exit node and the daemon
opens the final hop from the target namespace.
"""

from __future__ import annotations

import asyncio
import logging
import socket
import time
from dataclasses import dataclass, field

from netsim.system import socket_in_ns

log = logging.getLogger("netsim.proxy")

UDP_IDLE = 120.0
BUF = 65536


@dataclass
class Endpoint:
    ns: str | None
    host: str
    port: int


@dataclass
class ProxyStats:
    active: int = 0
    total: int = 0
    errors: int = 0
    bytes_up: int = 0    # client -> target
    bytes_down: int = 0  # target -> client
    last_error: str | None = None

    def to_json(self):
        return dict(self.__dict__)


@dataclass
class ProxySpec:
    name: str
    proto: str
    listen: Endpoint
    entry_ns: str | None
    exit_ns: str | None
    exit_ip: str
    target: Endpoint
    direct: bool  # entry connects straight to target (target inside exit node)


class _Base:
    def __init__(self, spec: ProxySpec, on_event=None):
        self.spec = spec
        self.stats = ProxyStats()
        self.on_event = on_event or (lambda *a: None)
        self._tasks: set[asyncio.Task] = set()

    def _spawn(self, coro):
        t = asyncio.create_task(coro)
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)
        return t

    def _fail(self, msg: str):
        self.stats.errors += 1
        self.stats.last_error = msg
        self.on_event("warn", f"proxy {self.spec.name}: {msg}")


def _bind(ns: str | None, host: str, port: int, kind: int) -> socket.socket:
    s = socket_in_ns(ns, socket.AF_INET, kind)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind((host, port))
    return s


class TcpProxy(_Base):
    async def start(self):
        sp = self.spec
        self._listener = _bind(sp.listen.ns, sp.listen.host, sp.listen.port, socket.SOCK_STREAM)
        self._listener.listen(128)
        self._server = await asyncio.start_server(self._client, sock=self._listener)
        self._relay = None
        if not sp.direct:
            relay_sock = _bind(sp.exit_ns, sp.exit_ip, 0, socket.SOCK_STREAM)
            relay_sock.listen(128)
            self._relay_addr = relay_sock.getsockname()
            self._relay = await asyncio.start_server(self._exit_side, sock=relay_sock)

    async def stop(self):
        for srv in (self._server, self._relay):
            if srv:
                srv.close()
        for t in list(self._tasks):
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    async def _connect(self, ns: str | None, host: str, port: int):
        s = socket_in_ns(ns, socket.AF_INET, socket.SOCK_STREAM)
        try:
            await asyncio.wait_for(asyncio.get_running_loop().sock_connect(s, (host, port)), 30)
        except BaseException:
            s.close()
            raise
        return await asyncio.open_connection(sock=s)

    async def _client(self, c_reader, c_writer):
        sp = self.spec
        self.stats.active += 1
        self.stats.total += 1
        try:
            dest = (sp.target.host, sp.target.port) if sp.direct else self._relay_addr
            try:
                u_reader, u_writer = await self._connect(sp.entry_ns, *dest)
            except (OSError, asyncio.TimeoutError) as e:
                self._fail(f"connect from entry node to {dest[0]}:{dest[1]}: {e or 'timeout'}")
                return
            await _splice(c_reader, c_writer, u_reader, u_writer, self.stats)
        finally:
            self.stats.active -= 1
            c_writer.close()

    async def _exit_side(self, r_reader, r_writer):
        sp = self.spec
        try:
            try:
                t_reader, t_writer = await self._connect(sp.target.ns, sp.target.host, sp.target.port)
            except (OSError, asyncio.TimeoutError) as e:
                self._fail(f"connect to target {sp.target.host}:{sp.target.port}: {e or 'timeout'}")
                return
            await _splice(r_reader, r_writer, t_reader, t_writer, None)
        finally:
            r_writer.close()


async def _splice(r1, w1, r2, w2, stats: ProxyStats | None):
    async def pump(reader, writer, attr):
        try:
            while data := await reader.read(BUF):
                writer.write(data)
                await writer.drain()
                if stats:
                    setattr(stats, attr, getattr(stats, attr) + len(data))
            if writer.can_write_eof():
                writer.write_eof()
        except (ConnectionError, OSError):
            writer.close()
    try:
        await asyncio.gather(pump(r1, w2, "bytes_up"), pump(r2, w1, "bytes_down"))
    finally:
        w1.close()
        w2.close()


class _UdpSession:
    def __init__(self, proxy: "UdpProxy", client: tuple):
        self.proxy, self.client = proxy, client
        self.last = time.monotonic()
        self.loop = loop = proxy.loop
        sp = proxy.spec
        self.socks: list[socket.socket] = []
        self.entry = self._sock(sp.entry_ns, "0.0.0.0")
        if sp.direct:
            self.entry.connect((sp.target.host, sp.target.port))
            self.exit = self.target = None
        else:
            self.exit = self._sock(sp.exit_ns, sp.exit_ip)
            self.target = self._sock(sp.target.ns, "0.0.0.0")
            self.target.connect((sp.target.host, sp.target.port))
            self.entry.connect(self.exit.getsockname())
            self.exit.connect(self.entry.getsockname())
            loop.add_reader(self.exit, self._exit_rx)
            loop.add_reader(self.target, self._target_rx)
        loop.add_reader(self.entry, self._entry_rx)

    def _sock(self, ns, host):
        s = socket_in_ns(ns, socket.AF_INET, socket.SOCK_DGRAM)
        s.bind((host, 0))
        self.socks.append(s)
        return s

    def send_up(self, data: bytes):
        self.last = time.monotonic()
        self.proxy.stats.bytes_up += len(data)
        _try_send(self.entry, data)

    def _exit_rx(self):  # entry -> exit arrived through the lab
        for data in _drain(self.exit):
            _try_send(self.target, data)

    def _target_rx(self):
        for data in _drain(self.target):
            _try_send(self.exit, data)

    def _entry_rx(self):  # reply arrived back at the entry node
        for data in _drain(self.entry):
            self.last = time.monotonic()
            self.proxy.stats.bytes_down += len(data)
            try:
                self.proxy.listener.sendto(data, self.client)
            except OSError:
                pass

    def close(self):
        for s in self.socks:
            self.loop.remove_reader(s)
            s.close()


def _drain(s: socket.socket):
    while True:
        try:
            yield s.recv(BUF)
        except (BlockingIOError, InterruptedError):
            return
        except OSError:
            return


def _try_send(s: socket.socket, data: bytes):
    try:
        s.send(data)
    except OSError:
        pass  # ICMP unreachable or buffer full: it's UDP


class UdpProxy(_Base):
    async def start(self):
        self.loop = asyncio.get_running_loop()
        sp = self.spec
        self.listener = _bind(sp.listen.ns, sp.listen.host, sp.listen.port, socket.SOCK_DGRAM)
        self.sessions: dict[tuple, _UdpSession] = {}
        self.loop.add_reader(self.listener, self._rx)
        self._spawn(self._reaper())

    def _rx(self):
        while True:
            try:
                data, addr = self.listener.recvfrom(BUF)
            except (BlockingIOError, InterruptedError):
                return
            except OSError:
                return
            sess = self.sessions.get(addr)
            if sess is None:
                try:
                    sess = self.sessions[addr] = _UdpSession(self, addr)
                except OSError as e:
                    self._fail(f"udp session: {e}")
                    continue
                self.stats.total += 1
                self.stats.active = len(self.sessions)
            sess.send_up(data)

    async def _reaper(self):
        while True:
            await asyncio.sleep(5)
            now = time.monotonic()
            for addr, sess in list(self.sessions.items()):
                if now - sess.last > UDP_IDLE:
                    sess.close()
                    del self.sessions[addr]
            self.stats.active = len(self.sessions)

    async def stop(self):
        self.loop.remove_reader(self.listener)
        self.listener.close()
        for sess in self.sessions.values():
            sess.close()
        self.sessions.clear()
        for t in list(self._tasks):
            t.cancel()


def make_proxy(spec: ProxySpec, on_event=None):
    return (TcpProxy if spec.proto == "tcp" else UdpProxy)(spec, on_event)
