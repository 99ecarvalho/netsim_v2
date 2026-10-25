# SPDX-License-Identifier: LGPL-3.0-or-later
# Copyright (C) 2026 Eduardo Correia
"""HTTP API tests in memory (no lifespan, so nothing touches the host)."""

from pathlib import Path

import httpx
import pytest

from netsim.bus import Bus
from netsim.engine import Engine
from netsim.server import create_app
from netsim.store import LabStore, ProfileStore

ROOT = Path(__file__).resolve().parent.parent
TOKEN = "test-token"


@pytest.fixture
async def client(tmp_path):
    engine = Engine(ProfileStore(ROOT / "profiles"), LabStore(tmp_path / "labs"), Bus(), tmp_path / "run")
    app = create_app(engine, TOKEN)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1",
                                 headers={"Authorization": f"Bearer {TOKEN}"}) as c:
        yield c


async def test_requires_token(client):
    r = await client.get("/api/info", headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 401


async def test_rejects_foreign_host(client):
    r = await client.get("/api/info", headers={"Host": "evil.example"})
    assert r.status_code == 403


async def test_validate_without_name(client):
    r = (await client.post("/api/profiles/validate", json={"text": "delay: 10ms"})).json()
    assert r["ok"], r
    r = (await client.post("/api/labs/validate", json={"text": "nodes: {a: {}}"})).json()
    assert r["ok"], r


@pytest.mark.parametrize("text, ok", [
    ("slot: {min: 1ms}", True),
    ("slot: {min: 5ms, max: 1ms}", False),
    ("delay: -1", False),
    ("delay: .nan", False),
    ("delay: .inf", False),
    ("rate: .inf", False),
    ("delay: 1e309ms", False),
    ("delay: 1e308h", False),
    ("rate: 1e400mbit", False),
    ("rate: 1e308gbit", False),
    ("queue: 1e309s\nrate: 1mbit", False),
    ("slot: {min: 1ms, bytes: 1e30mb}", False),
    ("delay: 1h", True),
])
async def test_profile_validation_edge_cases(client, text, ok):
    r = await client.post("/api/profiles/validate", json={"text": text})
    assert r.status_code == 200
    assert r.json()["ok"] is ok, r.json()


async def test_unknown_profile_points_at_link(client):
    r = (await client.post("/api/labs/validate", json={
        "text": "nodes: {a: {}, b: {}}\nlinks:\n  - {a: a, b: b, profile: nope}\n"})).json()
    assert not r["ok"] and r["details"][0]["loc"] == "links.0.profile"
