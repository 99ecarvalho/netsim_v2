# SPDX-License-Identifier: LGPL-3.0-or-later
# Copyright (C) 2026 Eduardo Correia
"""Retention limits are real maximums (no namespaces needed: items are registered directly)."""

from pathlib import Path

import pytest

from netsim import engine as eng
from netsim.bus import Bus
from netsim.engine import Capture, Engine, EngineError, Probe
from netsim.store import LabStore, ProfileStore


class FakeProc:
    def __init__(self, running):
        self.running = running


@pytest.fixture
def engine(tmp_path):
    return Engine(ProfileStore(tmp_path), LabStore(tmp_path), Bus(), tmp_path)


def add_capture(e, i, running):
    path = Path(e.run_dir) / f"c{i}.pcap"
    path.write_bytes(b"x")
    e.captures[f"c{i}"] = Capture(f"c{i}", "lab", "l", "a", "n", "eth0", "", path, float(i), proc=FakeProc(running))


def test_captures_never_exceed_the_limit(engine):
    for i in range(eng.KEEP_CAPTURES + 5):
        engine._make_room("capture", "lab")
        add_capture(engine, i, running=False)
        assert len(engine.captures) <= eng.KEEP_CAPTURES
    assert "c0" not in engine.captures and not (Path(engine.run_dir) / "c0.pcap").exists()


def test_running_items_are_kept_and_new_ones_refused(engine):
    for i in range(eng.KEEP_CAPTURES):
        add_capture(engine, i, running=True)
    with pytest.raises(EngineError, match="stop one first"):
        engine._make_room("capture", "lab")
    for i in range(eng.KEEP_PROBES):
        engine.probes[f"p{i}"] = Probe(f"p{i}", "lab", "ping", "a", "b", {}, float(i), running=i % 2 == 0)
    engine._make_room("probe", "lab")
    assert len(engine.probes) == eng.KEEP_PROBES - 1
    assert all(p.running or p.started > 1 for p in engine.probes.values())  # p1, the oldest finished, went
