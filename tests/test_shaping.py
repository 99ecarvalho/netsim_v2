# SPDX-License-Identifier: LGPL-3.0-or-later
# Copyright (C) 2026 Eduardo Correia
import statistics

import pytest

from netsim import distributions, units
from netsim.models import Link, Profile
from netsim.shaping import LinkShape, ShapingError, flatten_profile, netem


def test_units():
    assert units.parse_duration("1.5s") == 1500
    assert units.parse_duration("250us") == 0.25
    assert units.parse_duration(40) == 40
    assert units.parse_rate("2mbit") == 2_000_000
    assert units.parse_rate("2Mbps") == 2_000_000  # bits, unlike tc
    assert units.parse_rate("512kbit") == 512_000
    assert units.parse_percent("0.5%") == 0.5
    with pytest.raises(units.UnitError):
        units.parse_percent("150%")
    with pytest.raises(units.UnitError):
        units.parse_rate("10 furlongs")


def test_netem_basic():
    spec = netem({"delay": "100ms", "jitter": "20ms", "distribution": "normal", "loss": "1%", "rate": "1mbit"})
    args = " ".join(spec.args)
    assert "delay 100000us 20000us 0% distribution normal" in args
    assert "rate 1000000bit" in args
    assert "loss random 1% 0%" in args
    assert args.startswith("limit ")


def test_netem_preserve_order_uses_fast_rate():
    assert "rate 100000000000bit" in " ".join(netem({"delay": "10ms", "jitter": "5ms"}).args)
    assert "rate" not in netem({"delay": "10ms", "jitter": "5ms", "preserve_order": False}).args


def test_netem_queue_time():
    spec = netem({"rate": "12mbit", "queue": "1s"})
    # 12 Mbit/s for 1s = 1000 MTU packets
    assert int(spec.args[1]) == 1000


def test_netem_burst_loss_and_reorder_validation():
    args = netem({"burst_loss": {"p": "1%", "r": "25%"}}).args
    assert args[2:7] == ("loss", "gemodel", "1%", "25%", "100%")
    with pytest.raises(ShapingError):
        netem({"reorder": "5%"})
    assert netem({"blackhole": True}).args[-1] == "100%"


def test_sticky_signature_changes():
    a = netem({"delay": "10ms", "rate": "1mbit"}).sticky
    b = netem({"delay": "10ms"}).sticky
    assert a != b


def test_profile_extends_and_directions():
    profiles = {
        "base": Profile(name="base", delay="50ms", loss="1%", up={"rate": "1mbit"}, down={"rate": "5mbit"}),
        "child": Profile(name="child", extends="base", burst_loss={"p": "1%", "r": "20%"}, up={"rate": "2mbit"}),
    }
    flat = flatten_profile("child", profiles.__getitem__)
    shape = LinkShape(flat, Link(a="x", b="y"))
    up, down = shape.effective("up"), shape.effective("down")
    assert up["rate"] == "2mbit" and down["rate"] == "5mbit"
    assert "loss" not in up and up["burst_loss"]["p"] == "1%"
    link_override = LinkShape(flat, Link(a="x", b="y", delay="5ms", down={"rate": "9mbit"}))
    assert link_override.effective("up")["delay"] == "5ms"
    assert link_override.effective("down")["rate"] == "9mbit"


@pytest.mark.parametrize("name", list(distributions.GENERATED))
def test_generated_tables_are_standardised(name):
    t = distributions.table(name)
    assert len(t) == distributions.TABLE_SIZE
    z = [v / distributions.SCALE for v in t]
    assert abs(statistics.fmean(z)) < 0.05
    assert 0.85 < statistics.pstdev(z) < 1.05  # clamping at 4 sigma trims a little
