"""Turn profile/link parameters into netem configuration.

Each direction of a link is shaped by a single netem qdisc at the egress of the
sending interface. netem handles delay, jitter, loss, corruption, duplication,
reordering, slotting and rate (rate-limited packets wait in netem's queue, so
`queue` is the bottleneck buffer - make it big to get bufferbloat).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable, Literal

from netsim import units
from netsim.models import SHAPE_FIELDS, DirectionalParams, Link, Profile, ShapeParams, TimelineStep

Direction = Literal["up", "down"]

PRESERVE_ORDER_RATE = 100 * 10**9  # high enough to never limit, makes netem keep FIFO order
DEFAULT_QUEUE_MS = 100.0
AVG_PKT = 1000  # bytes, for in-flight estimates
MTU_PKT = 1500
MAX_LIMIT = 500_000


class ShapingError(ValueError):
    pass


# ---------------------------------------------------------------- param merging

def _fields(p: ShapeParams | None) -> dict[str, Any]:
    if p is None:
        return {}
    return {k: v for k, v in p.model_dump(exclude_none=True).items() if k in SHAPE_FIELDS}


def merge_params(*layers: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for layer in layers:
        for k, v in layer.items():
            if v is None:
                continue
            out[k] = v
            # loss and burst_loss are alternatives; the most specific layer wins
            if k == "loss":
                out.pop("burst_loss", None)
            elif k == "burst_loss":
                out.pop("loss", None)
    return out


def directional(p: DirectionalParams | None, direction: Direction) -> dict[str, Any]:
    if p is None:
        return {}
    return merge_params(_fields(p), _fields(getattr(p, direction)))


def flatten_profile(name: str, lookup: Callable[[str], Profile], _seen: tuple[str, ...] = ()) -> Profile:
    """Resolve `extends` chains into a single Profile."""
    if name in _seen:
        raise ShapingError(f"profile inheritance loop: {' -> '.join((*_seen, name))}")
    prof = lookup(name)
    if not prof.extends:
        return prof
    parent = flatten_profile(prof.extends, lookup, (*_seen, name))
    data = parent.model_dump(exclude_none=True)
    child = prof.model_dump(exclude_none=True, exclude_unset=True)
    for key, value in child.items():
        if key in ("up", "down") and key in data:
            data[key] = merge_params(data[key], value)
        elif key in SHAPE_FIELDS:
            data = merge_params(data, {key: value})
        else:
            data[key] = value
    data["extends"] = None
    data["name"] = prof.name
    return Profile.model_validate(data)


@dataclass
class LinkShape:
    """Everything needed to compute a link direction's effective params."""

    profile: Profile | None
    link: Link
    runtime: dict[Direction, dict[str, Any]] = field(default_factory=lambda: {"up": {}, "down": {}})

    def effective(self, direction: Direction, step: TimelineStep | None = None) -> dict[str, Any]:
        return merge_params(
            directional(self.profile, direction),
            directional(step, direction),
            directional(self.link, direction),
            self.runtime.get(direction, {}),
        )


# ---------------------------------------------------------------- netem rendering

@dataclass(frozen=True)
class NetemSpec:
    args: tuple[str, ...]
    sticky: tuple  # attributes netem keeps on `replace` when omitted
    summary: str

    def tc(self, dev: str) -> list[str]:
        return ["qdisc", "replace", "dev", dev, "root", "handle", "1:", "netem", *self.args]


def queue_packets(params: dict[str, Any], rate_bps: int | None) -> int | None:
    q = params.get("queue")
    if q is None:
        if not rate_bps:
            return None
        return max(32, math.ceil(rate_bps / 8 * DEFAULT_QUEUE_MS / 1000 / MTU_PKT))
    if isinstance(q, int) or (isinstance(q, str) and q.strip().isdigit()):
        return int(q)
    ms = units.parse_duration(q)
    if not rate_bps:
        return None  # no bottleneck, nothing to queue behind
    return max(8, math.ceil(rate_bps / 8 * ms / 1000 / MTU_PKT))


def netem(params: dict[str, Any]) -> NetemSpec:
    p = params
    args: list[str] = []
    summary: list[str] = []

    if p.get("blackhole"):
        return NetemSpec(("limit", "1000", "loss", "100%"), (False, None, False, None), "blackhole")

    delay = units.parse_duration(p.get("delay", 0))
    jitter = units.parse_duration(p.get("jitter", 0))
    dist = p.get("distribution") or "uniform"
    if delay or jitter:
        if jitter > delay and dist == "uniform":
            # uniform jitter is +/-jitter around delay; avoid nonsense negative delays
            jitter = delay
        args += ["delay", units.tc_time(delay), units.tc_time(jitter)]
        args.append(units.tc_percent(units.parse_percent(p.get("delay_correlation", 0))))
        summary.append(f"{delay:g}ms" + (f"±{jitter:g}ms" if jitter else ""))
    use_dist = dist if jitter and dist != "uniform" else None
    if use_dist:
        args += ["distribution", use_dist]
        summary[-1] += f" {use_dist}"

    rate = units.parse_rate(p["rate"]) if p.get("rate") else None
    tc_rate = rate
    if not rate and jitter and p.get("preserve_order", True):
        tc_rate = PRESERVE_ORDER_RATE
    if tc_rate:
        args += ["rate", f"{tc_rate}bit"]
    if rate:
        summary.append(units.fmt_rate(rate))

    if p.get("burst_loss"):
        bl = p["burst_loss"]
        pr = [units.parse_percent(bl[k]) if bl.get(k) is not None else d
              for k, d in (("p", None), ("r", None), ("bad_loss", 100.0), ("good_loss", 0.0))]
        args += ["loss", "gemodel", *(units.tc_percent(v) for v in pr)]
        avg = pr[0] / (pr[0] + pr[1]) * pr[2] + pr[1] / (pr[0] + pr[1]) * pr[3] if pr[0] + pr[1] else 0
        summary.append(f"burst-loss≈{avg:.2g}%")
    elif p.get("loss") is not None and units.parse_percent(p["loss"]) > 0:
        loss = units.parse_percent(p["loss"])
        args += ["loss", "random", units.tc_percent(loss),
                 units.tc_percent(units.parse_percent(p.get("loss_correlation", 0)))]
        summary.append(f"loss {loss:g}%")

    for key in ("corrupt", "duplicate"):
        if p.get(key) is not None and units.parse_percent(p[key]) > 0:
            v = units.parse_percent(p[key])
            args += [key, units.tc_percent(v),
                     units.tc_percent(units.parse_percent(p.get(f"{key}_correlation", 0)))]
            summary.append(f"{key} {v:g}%")

    if p.get("reorder") is not None and units.parse_percent(p["reorder"]) > 0:
        if not delay:
            raise ShapingError("'reorder' needs a non-zero 'delay'")
        v = units.parse_percent(p["reorder"])
        args += ["reorder", units.tc_percent(v),
                 units.tc_percent(units.parse_percent(p.get("reorder_correlation", 0)))]
        if p.get("reorder_gap"):
            args += ["gap", str(int(p["reorder_gap"]))]
        summary.append(f"reorder {v:g}%")

    slot = p.get("slot")
    if slot:
        args += ["slot", units.tc_time(units.parse_duration(slot["min"]))]
        if slot.get("max") is not None:
            args.append(units.tc_time(units.parse_duration(slot["max"])))
        if slot.get("packets"):
            args += ["packets", str(int(slot["packets"]))]
        if slot.get("bytes"):
            args += ["bytes", str(units.parse_size(slot["bytes"]))]
        summary.append("slotted")

    if p.get("ecn"):
        if "loss" not in args:
            raise ShapingError("'ecn' needs loss to be configured")
        args.append("ecn")

    # netem's limit covers packets in flight (delayed) plus the bottleneck queue
    queue = queue_packets(p, rate)
    span_ms = delay + 2 * jitter + (units.parse_duration(slot["max"] or slot["min"]) if slot else 0)
    if rate:
        in_flight = math.ceil(rate / 8 * span_ms / 1000 / AVG_PKT)
        limit = in_flight + (queue or 0)
        summary.append(f"queue {queue}p")
    else:
        # unlimited link: assume up to ~10 Gbit/s in flight so delay alone never drops
        limit = max(1000, math.ceil(1.25e9 * span_ms / 1000 / MTU_PKT))
        if queue:
            limit = queue
    args = ["limit", str(min(MAX_LIMIT, max(2, limit))), *args]

    sticky = (bool(tc_rate), bool(slot), bool(p.get("ecn")), use_dist)
    return NetemSpec(tuple(args), sticky, ", ".join(summary) or "no impairment")


def describe(params: dict[str, Any]) -> str:
    try:
        return netem(params).summary
    except (ShapingError, ValueError) as e:
        return f"invalid: {e}"
