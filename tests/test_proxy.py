# SPDX-License-Identifier: LGPL-3.0-or-later
# Copyright (C) 2026 Eduardo Correia
"""TCP proxy lifecycle without namespaces (all endpoints in the current namespace)."""

import asyncio

from netsim.proxy import Endpoint, ProxySpec, TcpProxy


async def test_stop_closes_open_connections():
    async def echo(reader, writer):
        while data := await reader.read(100):
            writer.write(data)
            await writer.drain()
    server = await asyncio.start_server(echo, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    spec = ProxySpec("t", "tcp", Endpoint(None, "127.0.0.1", 0), None, None, "127.0.0.1",
                     Endpoint(None, "127.0.0.1", port), direct=False)
    proxy = TcpProxy(spec)
    await proxy.start()
    listen_port = proxy._server.sockets[0].getsockname()[1]
    reader, writer = await asyncio.open_connection("127.0.0.1", listen_port)
    writer.write(b"hi")
    assert await asyncio.wait_for(reader.read(2), 2) == b"hi"
    assert proxy.stats.active == 1
    await asyncio.wait_for(proxy.stop(), 2)
    assert proxy.stats.active == 0 and not proxy._tasks
    assert await asyncio.wait_for(reader.read(10), 2) == b""  # the client sees the close
    server.close()
