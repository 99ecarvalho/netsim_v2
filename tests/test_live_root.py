# SPDX-License-Identifier: LGPL-3.0-or-later
# Copyright (C) 2026 Eduardo Correia
"""Integration tests that build real namespaces. Run with: sudo .venv/bin/pytest -p no:cacheprovider tests/test_live_root.py"""

import asyncio
import os
import subprocess
from pathlib import Path

import pytest

from netsim.bus import Bus
from netsim.engine import Engine
from netsim.store import LabStore, ProfileStore

pytestmark = pytest.mark.skipif(os.geteuid() != 0, reason="needs root")

ROOT = Path(__file__).resolve().parent.parent
LAB = """
nodes: {c: {}, s: {}}
links:
  - {a: c, b: s, profile: 4g-lte}
"""


def kernel_netem(ns: str, dev: str) -> str:
    return subprocess.run(["tc", "-n", ns, "qdisc", "show", "dev", dev], capture_output=True, text=True).stdout


@pytest.fixture
async def engine(tmp_path):
    labs = LabStore(tmp_path / "labs")
    labs.save("t-live", LAB)
    eng = Engine(ProfileStore(ROOT / "profiles"), labs, Bus(), tmp_path / "run")
    await eng.start(cleanup=False)
    yield eng
    await eng.stop()


async def test_profile_switch_clears_cut(engine):
    rt = await engine.up("t-live")
    await engine.set_link("t-live", "c--s", params={"both": {"blackhole": True}})
    assert "loss 100%" in kernel_netem(rt.iface_ns("c"), "eth0")
    await engine.set_link("t-live", "c--s", profile="gprs")
    out = kernel_netem(rt.iface_ns("c"), "eth0")
    assert "rate 20Kbit" in out and "loss 100%" not in out


async def test_fast_switches_end_consistent(engine):
    rt = await engine.up("t-live")
    names = ["flapping", "gsm-flaky", "ideal", "wifi-good", "satellite-leo", "gprs"] * 3
    await asyncio.gather(*(engine.set_link("t-live", "c--s", profile=n) for n in names))
    await engine.set_link("t-live", "c--s", profile="edge")
    assert "rate 100Kbit" in kernel_netem(rt.iface_ns("c"), "eth0")
    assert rt.links["c--s"].task is None


async def test_saved_profile_survives_restart_and_applies_live(engine):
    rt = await engine.up("t-live")
    text = LAB.replace("4g-lte", "5g-nr")
    engine.labs.save("t-live", text)
    result = await engine.adopt("t-live", text)
    assert result == {"live": ["c--s"], "restart": False}
    assert "rate 50Mbit" in kernel_netem(rt.iface_ns("c"), "eth0")
    await engine.down("t-live")
    rt = await engine.up("t-live")
    assert "rate 50Mbit" in kernel_netem(rt.iface_ns("c"), "eth0")
    assert (await engine.adopt("t-live", text + "  - {a: s, b: c, name: second}\n"))["restart"] is True


async def test_concurrent_starts_get_distinct_subnets(engine):
    engine.labs.save("t-par1", LAB)
    engine.labs.save("t-par2", LAB)
    a, b = await asyncio.gather(engine.up("t-par1"), engine.up("t-par2"))
    assert not a.plan.base.overlaps(b.plan.base)


async def test_restart_keeps_temporary_lab(engine):
    engine.labs.save("t-temp", LAB, ephemeral=True)
    await engine.up("t-temp")
    await engine.restart("t-temp")
    assert "t-temp" in engine.running and engine.labs.is_ephemeral("t-temp")
    await engine.down("t-temp")
    assert not engine.labs.exists("t-temp")


async def test_capture_stops_at_size_limit(engine, monkeypatch):
    from netsim import capture
    rt = await engine.up("t-live")
    cap = await engine.capture_start("t-live", "c--s")
    cap.proc.max_bytes = 2000
    await rt.runner.nsexec(rt.iface_ns("c"), "ping", "-c", "20", "-i", "0.01", "-s", "500", rt.plan.node_ip("s"))
    await asyncio.sleep(0.3)
    assert cap.proc.truncated and not cap.proc.running
    assert cap.path.stat().st_size <= 2000 + len(capture.PCAP_HEADER)


@pytest.fixture
def container():
    if subprocess.run(["docker", "image", "inspect", "nginx:alpine"], capture_output=True).returncode:
        pytest.skip("needs docker and the nginx:alpine image")
    name = "netsim-test-shared"
    subprocess.run(["docker", "rm", "-f", name], capture_output=True)
    subprocess.run(["docker", "run", "-d", "--rm", "--name", name, "nginx:alpine"], check=True, capture_output=True)
    yield name
    subprocess.run(["docker", "rm", "-f", name], capture_output=True)


async def test_container_cannot_join_two_labs(engine, container):
    from netsim.engine import EngineError
    text = f"nodes: {{c: {{}}, api: {{kind: container, container: {container}}}}}\nlinks:\n  - {{a: c, b: api}}\n"
    engine.labs.save("t-ctr-a", text)
    engine.labs.save("t-ctr-b", text)

    def ifaces():
        out = subprocess.run(["docker", "exec", container, "ip", "-o", "link"], capture_output=True, text=True)
        return sorted(line.split(":")[1].strip().split("@")[0] for line in out.stdout.splitlines())

    await engine.up("t-ctr-a")
    before = ifaces()
    assert any(i.startswith("sim") for i in before)
    with pytest.raises(EngineError, match="already in lab t-ctr-a"):
        await engine.up("t-ctr-b")
    assert ifaces() == before  # the failed lab did not touch the first one's interface
    await engine.down("t-ctr-a")
    assert not any(i.startswith("sim") for i in ifaces())
