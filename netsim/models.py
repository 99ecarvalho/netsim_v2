# SPDX-License-Identifier: LGPL-3.0-or-later
# Copyright (C) 2026 Eduardo Correia
"""Data models for profiles (link behaviour) and labs (topologies)."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from netsim import units

NAME_RE = r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,47}$"
NODE_RE = r"^[a-zA-Z][a-zA-Z0-9-]{0,23}$"

Distribution = Literal[
    "uniform", "normal", "pareto", "paretonormal",
    "exponential", "lognormal", "weibull", "experimental",
]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _check(parser, v):
    if v is None:
        return v
    parser(v)
    return v


class BurstLoss(_Strict):
    """Gilbert-Elliott two-state loss model (netem `loss gemodel`)."""

    p: str | float = Field(description="probability of moving good -> bad, per packet")
    r: str | float = Field(description="probability of moving bad -> good, per packet")
    bad_loss: str | float = Field("100%", description="loss probability while in bad state")
    good_loss: str | float = Field("0%", description="loss probability while in good state")

    @field_validator("p", "r", "bad_loss", "good_loss")
    @classmethod
    def _pct(cls, v):
        return _check(units.parse_percent, v)


class Slot(_Strict):
    """Deliver packets in bursts/slots, like Wi-Fi aggregation or LTE scheduling."""

    min: str | float = Field(description="minimum time between slots")
    max: str | float | None = Field(None, description="maximum time between slots")
    packets: int | None = Field(None, ge=1, description="max packets per slot")
    bytes: str | int | None = Field(None, description="max bytes per slot")

    @field_validator("min", "max")
    @classmethod
    def _dur(cls, v):
        return _check(units.parse_duration, v)

    @field_validator("bytes")
    @classmethod
    def _size(cls, v):
        return _check(units.parse_size, v)


class ShapeParams(_Strict):
    """Impairments applied to one direction of a link. All fields optional."""

    delay: str | float | None = Field(None, description="mean one-way delay (e.g. 40ms)")
    jitter: str | float | None = Field(None, description="delay variation; std-dev for non-uniform distributions")
    delay_correlation: str | float | None = Field(None, description="correlation between consecutive delays")
    distribution: Distribution | None = Field(None, description="shape of the delay variation")
    preserve_order: bool | None = Field(None, description="prevent jitter from reordering packets (default true)")
    rate: str | int | None = Field(None, description="bandwidth, bits per second (e.g. 2mbit)")
    queue: str | int | None = Field(None, description="bottleneck queue: packets (int) or time at link rate (e.g. 500ms)")
    loss: str | float | None = Field(None, description="random packet loss")
    loss_correlation: str | float | None = None
    burst_loss: BurstLoss | None = Field(None, description="bursty loss (Gilbert-Elliott); overrides loss")
    corrupt: str | float | None = Field(None, description="single-bit corruption probability")
    corrupt_correlation: str | float | None = None
    duplicate: str | float | None = Field(None, description="packet duplication probability")
    duplicate_correlation: str | float | None = None
    reorder: str | float | None = Field(None, description="probability a packet is sent immediately (overtaking delayed ones)")
    reorder_correlation: str | float | None = None
    reorder_gap: int | None = Field(None, ge=1, description="reorder every Nth packet instead of randomly")
    slot: Slot | None = None
    ecn: bool | None = Field(None, description="mark ECN-capable packets instead of dropping")
    blackhole: bool | None = Field(None, description="drop everything (link cut)")

    @field_validator("delay", "jitter")
    @classmethod
    def _dur(cls, v):
        return _check(units.parse_duration, v)

    @field_validator("rate")
    @classmethod
    def _rate(cls, v):
        if v is not None and units.parse_rate(v) <= 0:
            raise ValueError("rate must be > 0")
        return _check(units.parse_rate, v)

    @field_validator("queue")
    @classmethod
    def _queue(cls, v):
        if isinstance(v, str) and not v.strip().isdigit():
            units.parse_duration(v)
        elif v is not None and int(v) < 1:
            raise ValueError("queue must be >= 1 packet")
        return v

    @field_validator(
        "delay_correlation", "loss", "loss_correlation", "corrupt", "corrupt_correlation",
        "duplicate", "duplicate_correlation", "reorder", "reorder_correlation",
    )
    @classmethod
    def _pct(cls, v):
        return _check(units.parse_percent, v)

    def set_fields(self) -> dict[str, Any]:
        return self.model_dump(exclude_none=True)


SHAPE_FIELDS = tuple(ShapeParams.model_fields)


class DirectionalParams(ShapeParams):
    """ShapeParams plus optional per-direction overrides.

    For a link `a -- b`, `up` applies to traffic a -> b and `down` to b -> a.
    """

    up: ShapeParams | None = None
    down: ShapeParams | None = None


class TimelineStep(DirectionalParams):
    duration: str | float = Field(description="how long this step lasts")
    label: str | None = None

    @field_validator("duration")
    @classmethod
    def _duration(cls, v):
        if units.parse_duration(v) < 100:
            raise ValueError("step duration must be >= 100ms")
        return v


class Timeline(_Strict):
    loop: bool = True
    steps: list[TimelineStep] = Field(min_length=1)


class Profile(DirectionalParams):
    """A named, reusable link behaviour stored as profiles/<name>.yaml."""

    name: str | None = Field(None, pattern=NAME_RE)
    title: str | None = None
    description: str | None = None
    category: str | None = None
    tags: list[str] = Field(default_factory=list)
    extends: str | None = Field(None, description="inherit from another profile")
    timeline: Timeline | None = Field(None, description="time-varying behaviour applied on top of the base")


# --------------------------------------------------------------------------- labs

NodeKind = Literal["host", "router", "switch", "external", "container"]


class Node(_Strict):
    kind: NodeKind = "host"
    description: str | None = None
    icon: str | None = Field(None, description="UI hint: laptop, server, phone, iot, cloud, ...")
    internet: bool = Field(False, description="external only: NAT lab traffic to the host's default route")
    subnet: str | None = Field(None, description="switch only: CIDR shared by attached nodes")
    container: str | None = Field(None, description="container only: name or id of a running Docker/Podman container")
    sysctl: dict[str, str | int] = Field(default_factory=dict, description="extra sysctls inside the node")

    @model_validator(mode="after")
    def _kind_fields(self):
        if self.internet and self.kind != "external":
            raise ValueError("'internet' is only valid on external nodes")
        if self.subnet and self.kind != "switch":
            raise ValueError("'subnet' on a node is only valid for switches (put it on the link otherwise)")
        if (self.kind == "container") != bool(self.container):
            raise ValueError("container nodes need 'container: <name or id>' (and only they take it)")
        return self


class Link(DirectionalParams):
    a: str
    b: str
    name: str | None = Field(None, pattern=r"^[a-zA-Z0-9_-]{1,32}$")
    profile: str | None = Field(None, description="profile name; inline params override it")
    subnet: str | None = Field(None, description="CIDR for a point-to-point link")
    ip_a: str | None = Field(None, description="address of a on this link, CIDR")
    ip_b: str | None = Field(None, description="address of b on this link, CIDR")
    mtu: int | None = Field(None, ge=576, le=9000)

    @property
    def id(self) -> str:
        return self.name or f"{self.a}--{self.b}"


class Program(_Strict):
    name: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,32}$")
    node: str
    cmd: str
    cwd: str | None = None
    user: str | None = Field(None, description="run as this user; default is the user who started netsim; 'root' allowed")
    env: dict[str, str] = Field(default_factory=dict)
    autostart: bool = False
    restart: Literal["no", "on-failure", "always"] = "no"
    delay: str | float | None = Field(None, description="wait this long after lab start before autostart")

    @field_validator("delay")
    @classmethod
    def _dur(cls, v):
        return _check(units.parse_duration, v)


def parse_hostport(value: str, default_host: str = "127.0.0.1") -> tuple[str, int]:
    value = value.strip()
    if re.fullmatch(r"\d+", value):
        return default_host, int(value)
    m = re.fullmatch(r"\[?([^\]]+?)\]?:(\d+)", value)
    if not m:
        raise ValueError(f"expected [host:]port, got {value!r}")
    port = int(m.group(2))
    if not 0 < port < 65536:
        raise ValueError(f"port out of range in {value!r}")
    return m.group(1), port


class Proxy(_Strict):
    """Relay a TCP/UDP endpoint through the lab so only that traffic is shaped.

    listen (in listen_node, default: the real host) -> enters lab at via[0]
    -> shaped path -> leaves lab at via[1] -> target (in target_node, default host).
    """

    name: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,32}$")
    proto: Literal["tcp", "udp"] = "tcp"
    listen: str = Field(description="[addr:]port to accept on")
    listen_node: str | None = Field(None, description="node whose namespace hosts the listener; default host")
    target: str = Field(description="addr:port to forward to; addr may be a node name")
    target_node: str | None = Field(None, description="namespace the target is reached from; default host")
    via: list[str] | None = Field(None, min_length=2, max_length=2, description="[entry, exit] lab nodes")
    enabled: bool = True

    @field_validator("listen")
    @classmethod
    def _listen(cls, v):
        parse_hostport(v)
        return v

    @field_validator("target")
    @classmethod
    def _target(cls, v):
        parse_hostport(v)
        return v

    def endpoints(self, nodes: dict[str, Node]) -> tuple[str, str]:
        def lab_node(n):
            return n if n and nodes.get(n) and nodes[n].kind != "external" else None
        entry = self.via[0] if self.via else lab_node(self.listen_node)
        exit_ = self.via[1] if self.via else lab_node(self.target_node)
        if not entry or not exit_:
            raise ValueError(f"proxy {self.name}: set 'via: [entry, exit]' (cannot infer from listen/target nodes)")
        return entry, exit_


class Lab(_Strict):
    name: str | None = Field(None, pattern=NAME_RE)
    description: str | None = None
    subnet: str | None = Field(None, description="base CIDR for auto addressing (default: first free 10.N.0.0/16)")
    nodes: dict[str, Node]
    links: list[Link] = Field(default_factory=list)
    programs: list[Program] = Field(default_factory=list)
    proxies: list[Proxy] = Field(default_factory=list)
    layout: dict[str, dict[str, float]] = Field(default_factory=dict, description="UI node positions")

    @field_validator("nodes")
    @classmethod
    def _node_names(cls, v):
        for name in v:
            if not re.fullmatch(NODE_RE, name):
                raise ValueError(f"invalid node name {name!r}: letters, digits and '-', start with a letter, max 24")
        if sum(1 for n in v.values() if n.kind == "external") > 1:
            raise ValueError("at most one external node per lab")
        if not v:
            raise ValueError("a lab needs at least one node")
        return v

    @model_validator(mode="after")
    def _refs(self):
        ids = set()
        for link in self.links:
            for end in (link.a, link.b):
                if end not in self.nodes:
                    raise ValueError(f"link {link.id}: unknown node {end!r}")
            if link.a == link.b:
                raise ValueError(f"link {link.id}: a and b must differ")
            if link.id in ids:
                raise ValueError(f"duplicate link {link.id!r}; give parallel links a 'name'")
            ids.add(link.id)
            kinds = {self.nodes[link.a].kind, self.nodes[link.b].kind}
            if kinds == {"external", "switch"}:
                raise ValueError(f"link {link.id}: connect the external node to a router/host, not a switch")
        names = set()
        for p in self.programs:
            if p.node not in self.nodes:
                raise ValueError(f"program {p.name}: unknown node {p.node!r}")
            if self.nodes[p.node].kind == "switch":
                raise ValueError(f"program {p.name}: cannot run on a switch")
            if self.nodes[p.node].kind == "container":
                raise ValueError(f"program {p.name}: run programs in a container with docker exec")
            if p.name in names:
                raise ValueError(f"duplicate program name {p.name!r}")
            names.add(p.name)
        names = set()
        for px in self.proxies:
            for n in (px.listen_node, px.target_node, *(px.via or [])):
                if n is not None and n not in self.nodes:
                    raise ValueError(f"proxy {px.name}: unknown node {n!r}")
            entry, exit_ = px.endpoints(self.nodes)
            if entry == exit_:
                raise ValueError(f"proxy {px.name}: entry and exit must be different nodes")
            for n in (entry, exit_):
                if self.nodes[n].kind == "switch":
                    raise ValueError(f"proxy {px.name}: {n} is a switch")
            if px.name in names:
                raise ValueError(f"duplicate proxy name {px.name!r}")
            names.add(px.name)
        return self

    def link(self, link_id: str) -> Link:
        for link in self.links:
            if link.id == link_id:
                return link
        raise KeyError(link_id)
