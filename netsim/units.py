# SPDX-License-Identifier: LGPL-3.0-or-later
# Copyright (C) 2026 Eduardo Correia
"""Parsing of human-friendly quantities: durations, rates, percentages, sizes.

Conventions (explicit, because tc's own are a trap: in tc "mbps" means megaBYTES):
  * rates are always bits per second. "mbit", "mbps", "Mbit/s" all mean 10^6 bit/s.
  * plain numbers: durations are milliseconds, rates are bit/s, percentages are percent.
"""

from __future__ import annotations

import math
import re

_NUM = r"([0-9]*\.?[0-9]+(?:[eE][-+]?[0-9]+)?)"

_DURATION_UNITS = {
    "": 1.0, "ms": 1.0, "msec": 1.0,
    "us": 1e-3, "usec": 1e-3, "µs": 1e-3,
    "ns": 1e-6,
    "s": 1000.0, "sec": 1000.0,
    "min": 60_000.0, "m": 60_000.0,
    "h": 3_600_000.0,
}

_RATE_UNITS = {
    "": 1, "bit": 1, "bps": 1, "b": 1,
    "k": 10**3, "kbit": 10**3, "kbps": 10**3, "kb": 10**3,
    "m": 10**6, "mbit": 10**6, "mbps": 10**6, "mb": 10**6,
    "g": 10**9, "gbit": 10**9, "gbps": 10**9, "gb": 10**9,
}

_SIZE_UNITS = {
    "": 1, "b": 1,
    "k": 1024, "kb": 1024, "kib": 1024,
    "m": 1024**2, "mb": 1024**2, "mib": 1024**2,
}


class UnitError(ValueError):
    pass


def _split(value: str, what: str) -> tuple[float, str]:
    m = re.fullmatch(_NUM + r"\s*([a-zA-Zµ/%]*)", value.strip())
    if not m:
        raise UnitError(f"invalid {what}: {value!r}")
    return float(m.group(1)), m.group(2).lower()


def _number(value, what: str) -> float:
    """Plain numbers from YAML: must be finite and not negative (rejects -1, .nan, .inf)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise UnitError(f"invalid {what}: {value!r}")
    if not math.isfinite(value) or value < 0:
        raise UnitError(f"{what} must be a finite, non-negative number: {value!r}")
    return float(value)


def parse_duration(value: str | int | float) -> float:
    """Return milliseconds."""
    if not isinstance(value, str):
        return _number(value, "duration")
    num, unit = _split(value, "duration")
    if unit not in _DURATION_UNITS:
        raise UnitError(f"unknown duration unit {unit!r} in {value!r} (use us, ms, s, min)")
    return num * _DURATION_UNITS[unit]


def parse_rate(value: str | int | float) -> int:
    """Return bits per second."""
    if not isinstance(value, str):
        return int(_number(value, "rate"))
    num, unit = _split(value, "rate")
    unit = unit.removesuffix("/s")
    if unit not in _RATE_UNITS:
        raise UnitError(f"unknown rate unit {unit!r} in {value!r} (use kbit, mbit, gbit)")
    return int(num * _RATE_UNITS[unit])


def parse_percent(value: str | int | float) -> float:
    """Return percent in [0, 100]."""
    if not isinstance(value, str):
        pct = _number(value, "percentage")
    else:
        num, unit = _split(value, "percentage")
        if unit not in ("", "%"):
            raise UnitError(f"invalid percentage: {value!r}")
        pct = num
    if not 0 <= pct <= 100:
        raise UnitError(f"percentage out of range: {value!r}")
    return pct


def parse_size(value: str | int) -> int:
    """Return bytes."""
    if not isinstance(value, str):
        return int(_number(value, "size"))
    num, unit = _split(value, "size")
    if unit not in _SIZE_UNITS:
        raise UnitError(f"unknown size unit {unit!r} in {value!r}")
    return int(num * _SIZE_UNITS[unit])


def fmt_rate(bps: float) -> str:
    for div, unit in ((1e9, "Gbit/s"), (1e6, "Mbit/s"), (1e3, "kbit/s")):
        if bps >= div:
            return f"{bps / div:.3g} {unit}"
    return f"{bps:.0f} bit/s"


def tc_time(ms: float) -> str:
    """Format milliseconds for tc, using microseconds to keep precision."""
    return f"{round(ms * 1000)}us"


def tc_percent(pct: float) -> str:
    return f"{pct:.6g}%"
