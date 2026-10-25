# SPDX-License-Identifier: LGPL-3.0-or-later
# Copyright (C) 2026 Eduardo Correia
"""Runtime: builds labs on the host and keeps them alive.

One Engine per daemon. Every running lab has a LabRuntime holding its compiled
plan, the live shaping state of each link, its programs, proxies, captures and
probes. Everything observable is published on the Bus.
"""

from __future__ import annotations

import asyncio
import ipaddress
import itertools
import json
import logging
import os
import shlex
import shutil
import signal
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from netsim import parse, units
from netsim.bus import Bus
from netsim.capture import PacketCapture
from netsim.config import User, invoking_user, lookup_user
from netsim.distributions import install as install_distributions
from netsim.models import Lab, Link, Program, Proxy, ShapeParams, parse_hostport
from netsim.process import LineSplitter, PtyProcess, kill_tree, stream_lines
from netsim.proxy import Endpoint, ProxySpec, make_proxy
from netsim.shaping import Direction, LinkShape, NetemSpec, ShapingError, flatten_profile, netem
from netsim.store import LabStore, ProfileStore, StoreError, parse_yaml
from netsim import system
from netsim.system import CommandError, Runner, drop_privs_prefix, list_netns, nsexec_prefix
from netsim.topology import (ATTACHED_PREFIX, CONTAINER_IF_PREFIX, HOST_IF_PREFIX, NS_PREFIX, Plan, PlanError,
                             compile_lab, lab_tag, ns_lab)

log = logging.getLogger("netsim.engine")

STATS_INTERVAL = 1.0
STATS_HISTORY = 600
KEEP_CAPTURES = 10  # per lab; older stopped captures and their files are deleted
KEEP_PROBES = 20  # per lab
LOG_LINES = 5000
EVENTS = 1000
NETNS_ETC = Path("/etc/netns")
AUTO_BASES = [f"10.{k}.0.0/16" for k in range(201, 255)]
DIRECTIONS: tuple[Direction, Direction] = ("up", "down")


class EngineError(Exception):
    def __init__(self, message: str, loc: str | None = None):
        super().__init__(message)
        self.loc = loc  # path in the lab file, e.g. "links.2.profile", when known


# ------------------------------------------------------------------ runtime state

@dataclass
class LinkState:
    shape: LinkShape
    profile_name: str | None
    step: int | None = None
    step_label: str | None = None
    step_until: float | None = None
    applied: dict[str, NetemSpec] = field(default_factory=dict)
    task: asyncio.Task | None = None
    # serialises tc changes: a timeline step and a user change must not interleave
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def to_json(self):
        return {
            "profile": self.profile_name,
            "file_profile": self.shape.link.profile,
            "runtime": self.shape.runtime,
            "timeline": bool(self.shape.profile and self.shape.profile.timeline),
            "step": self.step, "step_label": self.step_label,
            "step_remaining": max(0.0, self.step_until - time.time()) if self.step_until else None,
            "up": {"summary": self.applied["up"].summary, "args": list(self.applied["up"].args)}
            if "up" in self.applied else None,
            "down": {"summary": self.applied["down"].summary, "args": list(self.applied["down"].args)}
            if "down" in self.applied else None,
        }


@dataclass
class ProgramState:
    spec: Program
    proc: PtyProcess | None = None
    status: str = "stopped"  # stopped | starting | running | exited | failed
    exit_code: int | None = None
    started_at: float | None = None
    restarts: int = 0
    stopping: bool = False
    lines: deque = field(default_factory=lambda: deque(maxlen=LOG_LINES))
    task: asyncio.Task | None = None

    def to_json(self):
        return {"name": self.spec.name, "node": self.spec.node, "cmd": self.spec.cmd, "status": self.status,
                "exit_code": self.exit_code, "started_at": self.started_at, "restarts": self.restarts,
                "pid": self.proc.pid if self.proc and self.status == "running" else None,
                "autostart": self.spec.autostart, "restart": self.spec.restart, "user": self.spec.user}


@dataclass
class ProxyState:
    spec: Proxy
    impl: Any = None
    error: str | None = None
    route: str | None = None

    def to_json(self):
        return {"name": self.spec.name, "proto": self.spec.proto, "listen": self.spec.listen,
                "listen_node": self.spec.listen_node, "target": self.spec.target,
                "target_node": self.spec.target_node, "route": self.route,
                "running": self.impl is not None, "error": self.error,
                "stats": self.impl.stats.to_json() if self.impl else None}


@dataclass
class Capture:
    id: str
    lab: str
    link: str
    side: str
    node: str
    iface: str
    filter: str
    path: Path
    started: float
    proc: Any = None
    packets: int = 0
    dropped: int = 0
    pending: list = field(default_factory=list)
    flusher: asyncio.Task | None = None

    def to_json(self):
        return {"id": self.id, "lab": self.lab, "link": self.link, "side": self.side, "node": self.node,
                "iface": self.iface, "filter": self.filter, "started": self.started, "packets": self.packets,
                "running": bool(self.proc and self.proc.running),
                "truncated": bool(self.proc and self.proc.truncated),
                "size": self.path.stat().st_size if self.path.exists() else 0}


@dataclass
class Probe:
    id: str
    lab: str
    kind: str
    src: str
    dst: str
    options: dict
    started: float
    procs: list = field(default_factory=list)
    results: deque = field(default_factory=lambda: deque(maxlen=3000))
    running: bool = True
    task: asyncio.Task | None = None

    def to_json(self, with_results=False):
        out = {"id": self.id, "lab": self.lab, "kind": self.kind, "src": self.src, "dst": self.dst,
               "options": self.options, "started": self.started, "running": self.running}
        if with_results:
            out["results"] = list(self.results)
        return out


@dataclass
class LabRuntime:
    name: str
    lab: Lab
    plan: Plan
    runner: Runner
    state: str = "starting"  # starting | running | stopping | failed
    started_at: float = field(default_factory=time.time)
    links: dict[str, LinkState] = field(default_factory=dict)
    programs: dict[str, ProgramState] = field(default_factory=dict)
    proxies: dict[str, ProxyState] = field(default_factory=dict)
    events: deque = field(default_factory=lambda: deque(maxlen=EVENTS))
    stats: deque = field(default_factory=lambda: deque(maxlen=STATS_HISTORY))
    counters: dict = field(default_factory=dict)
    iptables_rules: list[list[str]] = field(default_factory=list)
    containers: dict[str, tuple[int, str]] = field(default_factory=dict)  # node -> (pid, netns id)
    source_text: str = ""
    error: str | None = None

    @property
    def namespaces(self) -> list[str]:
        """Namespaces netsim owns (created and destroyed with the lab)."""
        return [n.ns for n in self.plan.nodes.values() if n.ns and n.kind != "container"]

    @property
    def attached(self) -> list[str]:
        """Container namespaces we only borrow: never kill processes or delete them."""
        return [n.ns for n in self.plan.nodes.values() if n.ns and n.kind == "container"]

    def iface_ns(self, node: str) -> str | None:
        return self.plan.nodes[node].ns


# ------------------------------------------------------------------ engine

class Engine:
    def __init__(self, profiles: ProfileStore, labs: LabStore, bus: Bus, run_dir: Path,
                 user: User | None = None, user_path: str | None = None):
        self.profiles, self.labs, self.bus = profiles, labs, bus
        self.run_dir = run_dir
        self.user = user or invoking_user()
        self.user_path = user_path or os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin")
        self.running: dict[str, LabRuntime] = {}
        self.captures: dict[str, Capture] = {}
        self.probes: dict[str, Probe] = {}
        self.failures: dict[str, str] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._alloc_lock = asyncio.Lock()
        self._stats_task: asyncio.Task | None = None
        self.global_runner = Runner()

    # ---------------------------------------------------------------- lifecycle

    async def start(self, cleanup: bool = True) -> None:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        captures = self.run_dir / "captures"
        shutil.rmtree(captures, ignore_errors=True)  # files of a previous run are unreachable anyway
        captures.mkdir(mode=0o700)
        system.TC_ENV["TC_LIB_DIR"] = str(install_distributions(self.run_dir / "tc"))
        system.init_root_ns_fd()
        if cleanup:  # off for tests, which must not touch a daemon running on the same host
            await self.cleanup_stale()
        self._stats_task = asyncio.create_task(self._stats_loop())

    async def stop(self) -> None:
        if self._stats_task:
            self._stats_task.cancel()
        for name in list(self.running):
            try:
                await self.down(name)
            except Exception as e:  # noqa: BLE001 - best effort on shutdown
                log.warning("teardown of %s failed: %s", name, e)
        for cid in list(self.captures):
            self.capture_delete(cid)

    async def cleanup_stale(self) -> list[str]:
        """Remove leftovers from a previous daemon (namespaces, host ifaces, nft tables, iptables rules)."""
        removed = []
        r = self.global_runner
        known = {ns for rt in self.running.values() for ns in rt.namespaces}
        known |= {ns for rt in self.running.values() for ns in rt.attached}
        for ns in await list_netns():
            if ns.startswith(NS_PREFIX) and ns not in known:
                await self._kill_ns_pids(r, ns)
                await r.run(["ip", "netns", "del", ns], check=False)
                shutil.rmtree(NETNS_ETC / ns, ignore_errors=True)
                removed.append(ns)
            elif ns.startswith(ATTACHED_PREFIX) and ns not in known:
                await self._detach(r, ns)
                removed.append(ns)
        known_tags = {lab_tag(n) for n in self.running}
        for link in await r.ip_json(None, "link", "show"):
            name = link.get("ifname", "")
            if name.startswith(HOST_IF_PREFIX) and name[3:8] not in known_tags:
                await r.ip(None, "link", "del", name, check=False)
                removed.append(name)
        if shutil.which("nft"):
            out = await r.run(["nft", "-j", "list", "tables"], check=False, quiet=True)
            try:
                for item in json.loads(out or "{}").get("nftables", []):
                    t = item.get("table")
                    if t and t["name"].startswith("netsim_") and t["name"][7:] not in known_tags:
                        await r.run(["nft", "delete", "table", t["family"], t["name"]], check=False)
                        removed.append(f"nft:{t['name']}")
            except ValueError:
                pass
        if shutil.which("iptables"):
            out = await r.run(["iptables", "-S", "FORWARD"], check=False, quiet=True)
            for line in out.splitlines():
                if "netsim:" in line and line.startswith("-A "):
                    tag = line.split("netsim:", 1)[1].split()[0].strip('"')
                    if tag not in known_tags:
                        await r.run(["iptables", "-D", *_split_rule(line[3:])], check=False)
                        removed.append(f"iptables:{tag}")
        if removed:
            log.info("cleaned up stale resources: %s", ", ".join(removed))
        return removed

    def _lock(self, name: str) -> asyncio.Lock:
        return self._locks.setdefault(name, asyncio.Lock())

    # ---------------------------------------------------------------- events

    def event(self, rt: LabRuntime | str, level: str, msg: str) -> None:
        name = rt if isinstance(rt, str) else rt.name
        ev = {"t": time.time(), "level": level, "msg": msg}
        if isinstance(rt, LabRuntime):
            rt.events.append(ev)
        getattr(log, "warning" if level == "warn" else level if level in ("info", "error") else "info")(
            "[%s] %s", name, msg)
        self.bus.publish(f"lab:{name}:event", ev)

    def _changed(self, rt: LabRuntime) -> None:
        self.bus.publish(f"lab:{rt.name}:runtime", {"t": time.time()})

    def _labs_changed(self) -> None:
        self.bus.publish("labs", {"labs": self.summary()})

    def summary(self) -> list[dict]:
        out = []
        for name in sorted(set(self.labs.names()) | set(self.running)):
            rt = self.running.get(name)
            entry = {"name": name, "state": rt.state if rt else ("failed" if name in self.failures else "stopped"),
                     "ephemeral": self.labs.is_ephemeral(name) if self.labs.exists(name) else True,
                     "error": rt.error if rt else self.failures.get(name)}
            try:
                e = self.labs.entry(name)
                entry["description"] = e.obj.description if e.obj else None
                entry["invalid"] = e.error is not None
                entry["modified"] = bool(rt and e.text != rt.source_text)
            except KeyError:
                pass
            out.append(entry)
        return out

    # ---------------------------------------------------------------- planning

    async def _used_networks(self, exclude: str | None = None, labs: bool = True) -> list[ipaddress.IPv4Network]:
        nets = []
        for rt in self.running.values() if labs else ():
            if rt.name != exclude:
                nets.append(rt.plan.base)
                nets += [s.subnet for s in rt.plan.segments]
        for route in await self.global_runner.ip_json(None, "route", "show", "table", "all"):
            dst = route.get("dst", "")
            if dst and dst != "default" and ":" not in dst:
                try:
                    nets.append(ipaddress.IPv4Network(dst if "/" in dst else dst + "/32", strict=False))
                except ValueError:
                    pass
        return nets

    async def allocate_base(self, lab: Lab, name: str) -> str:
        if lab.subnet:
            return lab.subnet
        used = await self._used_networks(exclude=name)
        for cand in AUTO_BASES:
            net = ipaddress.IPv4Network(cand)
            if not any(net.overlaps(u) for u in used if u.prefixlen > 0):
                return cand
        raise EngineError("no free 10.N.0.0/16 left for auto addressing; set 'subnet' on the lab")

    def link_shapes(self, lab: Lab) -> dict[str, LinkShape]:
        shapes = {}
        for index, link in enumerate(lab.links):
            prof = None
            if link.profile:
                try:
                    prof = flatten_profile(link.profile, self.profiles.get)
                except KeyError:
                    raise EngineError(f"link {link.id}: unknown profile {link.profile!r}", f"links.{index}.profile") from None
                except (StoreError, ShapingError) as e:
                    raise EngineError(f"link {link.id}: profile {link.profile}: {e}", f"links.{index}.profile") from None
            shape = LinkShape(prof, link)
            steps = prof.timeline.steps if prof and prof.timeline else [None]
            for step in steps:
                for d in DIRECTIONS:
                    try:
                        netem(shape.effective(d, step))
                    except (ShapingError, units.UnitError) as e:
                        raise EngineError(f"link {link.id} ({d}): {e}", f"links.{index}") from None
            shapes[link.id] = shape
        return shapes

    @staticmethod
    def _preflight(lab: Lab, plan: Plan) -> None:
        """Fail before creating anything when a needed tool is missing."""
        need = ["ip", "tc", "sysctl", "setpriv", "setsid"]
        if plan.internet:
            need.append("nft")
        if any(n.kind == "container" for n in lab.nodes.values()) and not (
                shutil.which("docker") or shutil.which("podman")):
            raise EngineError("container nodes need docker or podman (see `netsim doctor`)")
        missing = [t for t in need if not shutil.which(t)]
        if missing:
            raise EngineError(f"missing {', '.join(missing)} (see `netsim doctor`)")

    async def _check_conflicts(self, name: str, lab: Lab, plan: Plan) -> dict[str, tuple[int, str]]:
        """Refuse clashes with running labs (addresses, names, containers) and with host routes.

        Returns the containers to attach: node -> (pid, network namespace id).
        """
        containers: dict[str, tuple[int, str]] = {}
        taken = {netns: (other.name, node) for other in self.running.values() if other.name != name
                 for node, (_, netns) in other.containers.items()}
        for node in plan.nodes.values():
            if node.kind != "container":
                continue
            ref = lab.nodes[node.name].container or ""
            pid = await self._container_pid(self.global_runner, ref)
            try:
                netns = os.readlink(f"/proc/{pid}/ns/net")
            except OSError as e:
                raise EngineError(f"container {ref}: cannot read its network namespace: {e}") from None
            if netns in taken:
                other, other_node = taken[netns]
                raise EngineError(f"container {ref} is already in lab {other} (node {other_node}); "
                                  "a container can be in one running lab at a time")
            taken[netns] = (name, node.name)
            containers[node.name] = (pid, netns)
        self._check_names(name, plan)
        await self._check_subnets(name, plan)
        return containers

    def _check_names(self, name: str, plan: Plan) -> None:
        tag = lab_tag(name)
        for other in self.running.values():
            if other.name != name and lab_tag(other.name) == tag:
                raise EngineError(f"labs {name} and {other.name} map to the same interface names; rename one")

    async def _check_subnets(self, name: str, plan: Plan) -> None:
        if not plan.external:
            return  # namespaces are isolated: overlapping subnets cannot meet
        mine = [s.subnet for s in plan.segments]
        for other in self.running.values():
            if other.name == name or not other.plan.external:
                continue
            for net in mine:
                for theirs in (s.subnet for s in other.plan.segments):
                    if net.overlaps(theirs):
                        raise EngineError(f"subnet {net} overlaps {theirs} of running lab {other.name}")
        for route in await self._used_networks(exclude=name, labs=False):
            for net in mine:
                if route.prefixlen > 0 and net.overlaps(route):
                    raise EngineError(f"subnet {net} overlaps host route {route}; set another 'subnet'")

    async def preview(self, lab: Lab, name: str) -> dict:
        """Compile without touching the system (used by the editor)."""
        base = await self.allocate_base(lab, name) if name not in self.running else str(self.running[name].plan.base)
        plan = compile_lab(lab, name, base)
        shapes = self.link_shapes(lab)
        out = plan.to_json()
        for link_id, shape in shapes.items():
            out["links"][link_id]["shaping"] = {d: netem(shape.effective(d)).summary for d in DIRECTIONS}
        return out

    # ---------------------------------------------------------------- up / down

    async def up(self, name: str, text: str | None = None) -> LabRuntime:
        async with self._lock(name):
            if name in self.running:
                raise EngineError(f"lab {name} is already running")
            if os.geteuid() != 0:
                raise EngineError("netsim must run as root to build labs")
            source = text if text is not None else self.labs.text(name)
            lab = parse_yaml(source, Lab, name)
            # choosing a subnet and registering the lab is one step for all labs: two labs
            # starting together must not both see 10.201.0.0/16 as free
            async with self._alloc_lock:
                try:
                    shapes = self.link_shapes(lab)
                    base = await self.allocate_base(lab, name)
                    plan = compile_lab(lab, name, base)
                    self._preflight(lab, plan)
                    containers = await self._check_conflicts(name, lab, plan)
                except (PlanError, EngineError) as e:
                    self.failures[name] = str(e)
                    self._labs_changed()
                    raise EngineError(str(e)) from None
                rt = LabRuntime(name, lab, plan, Runner(), source_text=source, containers=containers)
                rt.runner.on_record = lambda rec: self.bus.publish(f"lab:{name}:cmd", rec.to_json())
                for link in lab.links:
                    rt.links[link.id] = LinkState(shapes[link.id], link.profile)
                for p in lab.programs:
                    rt.programs[p.name] = ProgramState(p)
                for px in lab.proxies:
                    rt.proxies[px.name] = ProxyState(px)
                self.running[name] = rt
            self.failures.pop(name, None)
            self._labs_changed()
            self.event(rt, "info", f"starting (subnet {base})")
            try:
                await self._build(rt)
            except Exception as e:
                rt.error = str(e)
                self.failures[name] = str(e)
                self.event(rt, "error", f"start failed: {e}")
                await self._teardown(rt)
                self.running.pop(name, None)
                self._labs_changed()
                raise EngineError(str(e)) from e
            rt.state = "running"
            self.event(rt, "info", "running")
            self._labs_changed()
            for ls_id, ls in rt.links.items():
                if ls.shape.profile and ls.shape.profile.timeline:
                    ls.task = asyncio.create_task(self._timeline(rt, ls_id))
            for px in rt.proxies.values():
                if px.spec.enabled:
                    await self._start_proxy(rt, px)
            for ps in rt.programs.values():
                if ps.spec.autostart:
                    delay = units.parse_duration(ps.spec.delay) / 1000 if ps.spec.delay else 0
                    ps.task = asyncio.create_task(self._delayed_start(rt, ps, delay))
            return rt

    async def down(self, name: str, forget_ephemeral: bool = True) -> None:
        async with self._lock(name):
            rt = self.running.get(name)
            if not rt:
                raise EngineError(f"lab {name} is not running")
            rt.state = "stopping"
            self._labs_changed()
            await self._teardown(rt)
            self.running.pop(name, None)
            self.event(name, "info", "stopped")
            if forget_ephemeral and self.labs.is_ephemeral(name):
                self.labs.delete(name)
            self._labs_changed()

    async def restart(self, name: str) -> LabRuntime:
        if name in self.running:
            await self.down(name, forget_ephemeral=False)  # a temporary lab survives its restart
        return await self.up(name)

    async def _build(self, rt: LabRuntime) -> None:
        r, plan, lab = rt.runner, rt.plan, rt.lab
        existing = await list_netns()
        for ns in rt.namespaces:
            if ns in existing:
                await self._kill_ns_pids(r, ns)
                await r.run(["ip", "netns", "del", ns], check=False)
        for ns in rt.attached:
            if ns in existing:
                await self._detach(r, ns)
        # containers: borrow their network namespace
        for node in plan.nodes.values():
            if node.kind == "container":
                pid, _ = rt.containers[node.name]  # resolved and checked for sharing in _check_conflicts
                await r.run(["ip", "netns", "attach", node.ns, str(pid)])
        # namespaces
        for node in plan.nodes.values():
            if not node.ns or node.kind == "container":
                continue
            await r.run(["ip", "netns", "add", node.ns])
            await r.ip(node.ns, "link", "set", "lo", "up")
            sysctls = {"net.ipv4.ip_forward": 1, "net.ipv6.conf.all.disable_ipv6": 1,
                       "net.ipv6.conf.default.disable_ipv6": 1, "net.ipv4.conf.all.rp_filter": 0,
                       "net.ipv4.conf.default.rp_filter": 0, "net.ipv4.ping_group_range": "0 2147483647"}
            sysctls.update(lab.nodes[node.name].sysctl)
            await r.nsexec(node.ns, "sysctl", "-q", "-w", *(f"{k}={v}" for k, v in sysctls.items()))
            if node.kind == "switch":
                await r.ip_batch(node.ns, ["link add name br0 type bridge stp_state 0 forward_delay 0",
                                           "link set br0 up"])
            else:
                self._write_etc(node.ns, plan)
        # links
        for lp in plan.links.values():
            a_ns, b_ns = rt.iface_ns(lp.a.node), rt.iface_ns(lp.b.node)
            argv = ["link", "add", "name", lp.a.name, *(["netns", a_ns] if a_ns else []),
                    "type", "veth", "peer", "name", lp.b.name, *(["netns", b_ns] if b_ns else [])]
            await r.ip(None, *argv)
            for end, ns in ((lp.a, a_ns), (lp.b, b_ns)):
                cmds = []
                if end.mtu:
                    cmds.append(f"link set {end.name} mtu {end.mtu}")
                if plan.nodes[end.node].kind == "switch":
                    cmds.append(f"link set {end.name} master br0")
                elif end.ip:
                    cmds.append(f"addr add {end.ip} dev {end.name}")
                cmds.append(f"link set {end.name} up")
                await r.ip_batch(ns, cmds)
        # routes
        for node in plan.nodes.values():
            if node.kind == "switch":
                continue
            await r.ip_batch(node.ns, [f"route replace {rt_.dst} via {rt_.via} dev {rt_.dev}" for rt_ in node.routes])
        if plan.external:
            await self._host_firewall(rt)
        # shaping
        for link_id in rt.links:
            await self._apply(rt, link_id)

    def _write_etc(self, ns: str, plan: Plan) -> None:
        d = NETNS_ETC / ns
        d.mkdir(parents=True, exist_ok=True)
        lines = ["127.0.0.1\tlocalhost", "::1\tlocalhost ip6-localhost"]
        lines += [f"{ip}\t{name}" for name, ip in plan.hosts().items()]
        (d / "hosts").write_text("\n".join(lines) + "\n")
        (d / "resolv.conf").write_text(_resolv_conf())
        (d / "nsswitch.conf").write_text(_nsswitch_conf())

    async def _host_firewall(self, rt: LabRuntime) -> None:
        """Let lab traffic through the host; NAT it out when the external node has internet."""
        r, tag, plan = rt.runner, lab_tag(rt.name), rt.plan
        await r.run(["sysctl", "-q", "-w", "net.ipv4.ip_forward=1"])
        mine, any_lab = f"{HOST_IF_PREFIX}{tag}+", f"{HOST_IF_PREFIX}+"
        rules = [["-i", mine, "-o", any_lab], ["-i", any_lab, "-o", mine]]
        if plan.internet:
            rules += [["-i", mine], ["-o", mine, "-m", "conntrack", "--ctstate", "ESTABLISHED,RELATED"]]
        if shutil.which("iptables"):
            for rule in rules:
                full = [*rule, "-m", "comment", "--comment", f"netsim:{tag}", "-j", "ACCEPT"]
                await r.run(["iptables", "-I", "FORWARD", *full])
                rt.iptables_rules.append(full)
        if shutil.which("nft"):
            nets = ", ".join(str(s.subnet) for s in plan.segments)
            script = [f"table ip netsim_{tag} {{",
                      "  chain forward { type filter hook forward priority -10; policy accept;"]
            if not plan.internet:
                script.append(f'    iifname "{HOST_IF_PREFIX}{tag}*" oifname != "{HOST_IF_PREFIX}*" drop')
            script.append("  }")
            if plan.internet:
                script += ["  chain postrouting { type nat hook postrouting priority 100; policy accept;",
                           f'    ip saddr {{ {nets} }} oifname != "{HOST_IF_PREFIX}*" masquerade', "  }"]
            script.append("}")
            await r.run(["nft", "-f", "-"], input="\n".join(script) + "\n")
        elif plan.internet:
            raise EngineError("internet access needs nft")

    async def _teardown(self, rt: LabRuntime) -> None:
        r = rt.runner
        for ls in rt.links.values():
            if ls.task:
                ls.task.cancel()
        for px in rt.proxies.values():
            await self._stop_proxy(rt, px)
        await asyncio.gather(*(self._stop_program(rt, ps, quiet=True) for ps in rt.programs.values()),
                             return_exceptions=True)
        for cap in [c for c in self.captures.values() if c.lab == rt.name]:
            await self.capture_stop(cap.id)
        for probe in [p for p in self.probes.values() if p.lab == rt.name]:
            await self.probe_stop(probe.id)
            self.probes.pop(probe.id, None)  # results belong to this run of the lab
        existing = await list_netns()
        for ns in rt.attached:
            if ns in existing:
                await self._detach(r, ns)
        for ns in rt.namespaces:
            if ns in existing:
                await self._kill_ns_pids(r, ns)
                await r.run(["ip", "netns", "del", ns], check=False)
            shutil.rmtree(NETNS_ETC / ns, ignore_errors=True)
        tag = lab_tag(rt.name)
        for node in rt.plan.nodes.values():
            if node.kind == "external":
                for iface in node.ifaces:
                    await r.ip(None, "link", "del", iface.name, check=False, quiet=True)
        for rule in rt.iptables_rules:
            await r.run(["iptables", "-D", "FORWARD", *rule], check=False)
        rt.iptables_rules.clear()
        if rt.plan.external and shutil.which("nft"):
            await r.run(["nft", "delete", "table", "ip", f"netsim_{tag}"], check=False)

    async def _container_pid(self, r: Runner, ref: str) -> int:
        for tool in ("docker", "podman"):
            if not shutil.which(tool):
                continue
            out = await r.run([tool, "inspect", "-f", "{{.State.Pid}}", ref], check=False, quiet=True)
            if out.strip().isdigit() and int(out.strip()) > 0:
                return int(out.strip())
        raise EngineError(f"container {ref!r} not found or not running (docker/podman inspect)")

    async def _detach(self, r: Runner, ns: str) -> None:
        """Remove this lab's interfaces from a container namespace and forget its name.

        Only interfaces carrying the lab's tag are touched: never another lab's, never Docker's.
        """
        lab = ns_lab(ns)
        if lab is None:
            return
        prefix = f"{CONTAINER_IF_PREFIX}{lab_tag(lab)}"
        for link in await r.ip_json(ns, "link", "show"):
            name = link.get("ifname", "")
            if name.startswith(prefix):
                await r.ip(ns, "link", "del", name, check=False)
        await r.run(["ip", "netns", "del", ns], check=False)  # only unmounts: the container keeps it alive

    async def _kill_ns_pids(self, r: Runner, ns: str) -> None:
        out = await r.run(["ip", "netns", "pids", ns], check=False, quiet=True)
        pids = [int(p) for p in out.split() if p.isdigit()]
        for sig in (signal.SIGTERM, signal.SIGKILL):
            alive = []
            for pid in pids:
                try:
                    os.kill(pid, sig)
                    alive.append(pid)
                except ProcessLookupError:
                    pass
            if not alive:
                return
            await asyncio.sleep(0.5)
            pids = [p for p in alive if _pid_alive(p)]

    # ---------------------------------------------------------------- shaping

    async def _apply(self, rt: LabRuntime, link_id: str) -> None:
        ls = rt.links[link_id]
        async with ls.lock:
            lp = rt.plan.links[link_id]
            step = None
            if ls.step is not None and ls.shape.profile and ls.shape.profile.timeline:
                step = ls.shape.profile.timeline.steps[ls.step]
            for direction, end in (("up", lp.a), ("down", lp.b)):
                spec = netem(ls.shape.effective(direction, step))
                prev = ls.applied.get(direction)
                if prev and prev.args == spec.args:
                    continue
                ns = rt.iface_ns(end.node)
                # forget what was applied until tc succeeds, so a failure is retried next time
                ls.applied.pop(direction, None)
                if prev and prev.sticky != spec.sticky:
                    await rt.runner.tc(ns, "qdisc", "del", "dev", end.name, "root", check=False)
                await rt.runner.tc(ns, *spec.tc(end.name))
                ls.applied[direction] = spec

    async def _timeline(self, rt: LabRuntime, link_id: str) -> None:
        ls = rt.links[link_id]
        tl = ls.shape.profile.timeline
        try:
            for cycle in itertools.count():
                for i, step in enumerate(tl.steps):
                    dur = units.parse_duration(step.duration) / 1000
                    ls.step, ls.step_label, ls.step_until = i, step.label or f"step {i + 1}", time.time() + dur
                    await self._apply(rt, link_id)
                    self.event(rt, "info", f"{link_id}: {ls.step_label} ({step.duration})")
                    self._changed(rt)
                    await asyncio.sleep(dur)
                if not tl.loop:
                    break
            ls.step = ls.step_label = ls.step_until = None
            await self._apply(rt, link_id)
            self._changed(rt)
        except asyncio.CancelledError:
            pass
        except Exception as e:  # noqa: BLE001 - keep the lab alive, report the problem
            self.event(rt, "error", f"{link_id}: timeline stopped: {e}")

    async def _stop_timeline(self, ls: LinkState) -> None:
        if ls.task:
            ls.task.cancel()
            await asyncio.gather(ls.task, return_exceptions=True)
            ls.task = None
        ls.step = ls.step_label = ls.step_until = None

    async def _reconfigure(self, rt: LabRuntime, link_id: str, profile: str | None, link: Link | None = None,
                           reset_overrides: bool = True) -> None:
        """Switch a link to another profile and/or link definition, restarting its timeline."""
        ls = rt.links[link_id]
        try:
            new_profile = flatten_profile(profile, self.profiles.get) if profile else None
        except KeyError:
            raise EngineError(f"unknown profile {profile!r}") from None
        except (StoreError, ShapingError) as e:
            raise EngineError(f"profile {profile}: {e}") from None
        candidate = LinkShape(new_profile, link or ls.shape.link,
                              {"up": {}, "down": {}} if reset_overrides else ls.shape.runtime)
        steps = new_profile.timeline.steps if new_profile and new_profile.timeline else [None]
        try:
            for step in steps:
                for d in DIRECTIONS:
                    netem(candidate.effective(d, step))
        except (ShapingError, units.UnitError) as e:
            raise EngineError(f"{link_id}: {e}") from None
        await self._stop_timeline(ls)
        ls.shape, ls.profile_name = candidate, profile or None
        await self._apply(rt, link_id)
        if new_profile and new_profile.timeline:
            ls.task = asyncio.create_task(self._timeline(rt, link_id))

    async def set_link(self, name: str, link_id: str, *, profile: str | None | bool = False,
                       params: dict | None = None, reset: bool = False) -> dict:
        """Change a running link. profile=False keeps it; params = {"both"|"up"|"down": {...}}.

        Switching profile drops live overrides (including a cut): the new profile is what you get.
        """
        rt = self._rt(name)
        if link_id not in rt.links:
            raise EngineError(f"unknown link {link_id}")
        ls = rt.links[link_id]
        if profile is not False:
            await self._reconfigure(rt, link_id, profile or None)
        if reset:
            ls.shape.runtime = {"up": {}, "down": {}}
        if params:
            runtime = {d: dict(v) for d, v in ls.shape.runtime.items()}
            for key, values in params.items():
                ShapeParams.model_validate({k: v for k, v in values.items() if v is not None})
                for d in (DIRECTIONS if key == "both" else (key,)):
                    merged = {**runtime.get(d, {}), **values}
                    runtime[d] = {k: v for k, v in merged.items() if v is not None}
            try:
                for d in DIRECTIONS:
                    netem(LinkShape(ls.shape.profile, ls.shape.link, runtime).effective(d))
            except (ShapingError, units.UnitError) as e:
                raise EngineError(str(e)) from None
            ls.shape.runtime = runtime
        await self._apply(rt, link_id)
        self.event(rt, "info", f"{link_id}: {ls.applied['up'].summary} / {ls.applied['down'].summary}")
        self._changed(rt)
        return ls.to_json()

    async def adopt(self, name: str, text: str) -> dict:
        """A running lab's file was saved: apply what can change live, report the rest.

        Profiles and shaping fields of links, descriptions and layout apply immediately;
        anything else (nodes, wiring, addresses, programs, proxies) needs a restart.
        """
        rt = self.running.get(name)
        if not rt:
            return {"live": [], "restart": False}
        new = parse_yaml(text, Lab, name)
        old = rt.lab

        def wiring(lab: Lab):
            return (lab.model_dump(exclude={"description", "layout", "links", "name"}),
                    [(lk.a, lk.b, lk.name, lk.subnet, lk.ip_a, lk.ip_b, lk.mtu) for lk in lab.links])

        if wiring(new) != wiring(old):
            return {"live": [], "restart": True}
        changed = []
        for new_link, old_link in zip(new.links, old.links):
            if new_link.model_dump() != old_link.model_dump():
                same_profile = new_link.profile == old_link.profile
                await self._reconfigure(rt, new_link.id, new_link.profile, new_link, reset_overrides=not same_profile)
                changed.append(new_link.id)
        rt.lab, rt.source_text = new, text
        for link_id in changed:
            ls = rt.links[link_id]
            self.event(rt, "info", f"{link_id}: saved change applied live - {ls.applied['up'].summary}")
        self._changed(rt)
        self._labs_changed()
        return {"live": changed, "restart": False}

    async def profile_saved(self, profile: str) -> list[str]:
        """Re-apply running links whose profile (or one it extends) changed on disk."""
        touched = []
        for rt in list(self.running.values()):
            for link_id, ls in rt.links.items():
                if not ls.profile_name:
                    continue
                try:
                    fresh = flatten_profile(ls.profile_name, self.profiles.get)
                except (KeyError, StoreError, ShapingError):
                    continue
                if ls.shape.profile is None or fresh.model_dump() != ls.shape.profile.model_dump():
                    try:
                        await self._reconfigure(rt, link_id, ls.profile_name, reset_overrides=False)
                    except EngineError as e:
                        self.event(rt, "error", f"{link_id}: updated profile {profile} not applied: {e}")
                        continue
                    self.event(rt, "info", f"{link_id}: profile {ls.profile_name} updated on disk, re-applied")
                    touched.append(f"{rt.name}/{link_id}")
                    self._changed(rt)
        return touched

    # ---------------------------------------------------------------- programs

    def _proc_env(self, user: User, lab: str, node: str, extra: dict | None = None) -> dict[str, str]:
        path = self.user_path if user.uid != 0 else "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
        env = {"PATH": path, "HOME": user.home, "USER": user.name, "LOGNAME": user.name,
               "SHELL": "/bin/bash", "TERM": "xterm-256color", "LANG": os.environ.get("LANG", "C.UTF-8"),
               "NETSIM_LAB": lab, "NETSIM_NODE": node, "PYTHONUNBUFFERED": "1"}
        env.update(extra or {})
        return env

    def _user_for(self, name: str | None) -> User:
        if not name:
            return self.user
        if name == "root":
            return User(0, 0, "root", "/root")
        try:
            return lookup_user(name)
        except KeyError:
            raise EngineError(f"unknown user {name!r}") from None

    def node_argv(self, rt: LabRuntime, node: str, user: User, argv: list[str]) -> list[str]:
        return [*nsexec_prefix(rt.iface_ns(node)), *drop_privs_prefix(user.uid, user.gid), *argv]

    async def _delayed_start(self, rt: LabRuntime, ps: ProgramState, delay: float) -> None:
        if delay:
            await asyncio.sleep(delay)
        await self._start_program(rt, ps)

    async def _start_program(self, rt: LabRuntime, ps: ProgramState) -> None:
        if ps.status in ("running", "starting"):
            return
        user = self._user_for(ps.spec.user)
        topic = f"program:{rt.name}:{ps.spec.name}"

        def emit(line: str):
            entry = {"t": time.time(), "line": line}
            ps.lines.append(entry)
            self.bus.publish(topic, entry)

        splitter = LineSplitter(emit)
        ps.stopping = False

        def on_exit(code: int):
            splitter.flush()
            ps.exit_code = code
            ps.status = "exited" if code == 0 or ps.stopping else "failed"
            emit(f"[netsim] process exited with code {code}")
            self.event(rt, "info" if ps.status == "exited" else "warn",
                       f"program {ps.spec.name} exited ({code})")
            self._changed(rt)
            policy = ps.spec.restart
            if not ps.stopping and rt.state == "running" and (
                    policy == "always" or policy == "on-failure" and code != 0):
                ps.restarts += 1
                ps.task = asyncio.create_task(self._delayed_start(rt, ps, min(10, ps.restarts)))

        cwd = ps.spec.cwd or user.home
        argv = self.node_argv(rt, ps.spec.node, user, ["/bin/sh", "-c", ps.spec.cmd])
        ps.proc = PtyProcess(argv, self._proc_env(user, rt.name, ps.spec.node, ps.spec.env),
                             cwd=os.path.expanduser(cwd) if os.path.isdir(os.path.expanduser(cwd)) else "/",
                             on_output=splitter.feed, on_exit=on_exit)
        ps.status = "starting"
        emit(f"[netsim] $ {ps.spec.cmd}   (node {ps.spec.node}, user {user.name})")
        try:
            await ps.proc.start()
        except OSError as e:
            ps.status = "failed"
            emit(f"[netsim] failed to start: {e}")
            self._changed(rt)
            return
        ps.status, ps.started_at, ps.exit_code = "running", time.time(), None
        self.event(rt, "info", f"program {ps.spec.name} started on {ps.spec.node}")
        self._changed(rt)

    async def _stop_program(self, rt: LabRuntime, ps: ProgramState, quiet: bool = False) -> None:
        ps.stopping = True
        if ps.task:
            ps.task.cancel()
            ps.task = None
        if ps.proc and ps.status == "running":
            await ps.proc.stop()
        if not quiet:
            self._changed(rt)

    async def program_action(self, name: str, prog: str, action: str) -> dict:
        rt = self._rt(name)
        ps = rt.programs.get(prog)
        if not ps:
            raise EngineError(f"unknown program {prog}")
        if action in ("stop", "restart"):
            await self._stop_program(rt, ps)
        if action in ("start", "restart"):
            ps.restarts = 0
            await self._start_program(rt, ps)
        if action == "signal":
            ps.proc and ps.proc.signal(signal.SIGINT)
        return ps.to_json()

    def program_input(self, name: str, prog: str, data: str) -> None:
        ps = self._rt(name).programs.get(prog)
        if ps and ps.proc:
            ps.proc.write(data.encode())

    # ---------------------------------------------------------------- proxies

    def _resolve_proxy(self, rt: LabRuntime, px: Proxy) -> ProxySpec:
        nodes, plan = rt.lab.nodes, rt.plan
        entry, exit_ = px.endpoints(nodes)

        def ns_of(node: str | None) -> str | None:
            return plan.nodes[node].ns if node else None

        lhost, lport = parse_hostport(px.listen)
        thost, tport = parse_hostport(px.target)
        target_node = px.target_node
        if thost in plan.nodes:
            if target_node is None and nodes[thost].kind != "external":
                target_node = thost
            thost = plan.node_ip(thost, toward=exit_)
        direct = target_node == exit_
        exit_ip = plan.node_ip(exit_, toward=entry)
        return ProxySpec(px.name, px.proto, Endpoint(ns_of(px.listen_node), lhost, lport),
                         ns_of(entry), ns_of(exit_), exit_ip, Endpoint(ns_of(target_node), thost, tport), direct)

    async def _start_proxy(self, rt: LabRuntime, ps: ProxyState) -> None:
        try:
            spec = self._resolve_proxy(rt, ps.spec)
            entry, exit_ = ps.spec.endpoints(rt.lab.nodes)
            ps.route = f"{ps.spec.listen_node or 'host'}:{ps.spec.listen} -> {entry} ~> {exit_} -> " \
                       f"{ps.spec.target_node or (ps.spec.target.split(':')[0] if spec.direct else 'host')}:{spec.target.port}"
            impl = make_proxy(spec, lambda lvl, msg: self.event(rt, lvl, msg))
            await impl.start()
            ps.impl, ps.error = impl, None
            self.event(rt, "info", f"proxy {ps.spec.name}: {ps.route}")
        except (OSError, ValueError, PlanError) as e:
            ps.error = str(e)
            self.event(rt, "error", f"proxy {ps.spec.name} failed: {e}")
        self._changed(rt)

    async def _stop_proxy(self, rt: LabRuntime, ps: ProxyState) -> None:
        if ps.impl:
            await ps.impl.stop()
            ps.impl = None

    async def proxy_action(self, name: str, proxy: str, action: str) -> dict:
        rt = self._rt(name)
        ps = rt.proxies.get(proxy)
        if not ps:
            raise EngineError(f"unknown proxy {proxy}")
        if action in ("stop", "restart"):
            await self._stop_proxy(rt, ps)
        if action in ("start", "restart") and not ps.impl:
            await self._start_proxy(rt, ps)
        self._changed(rt)
        return ps.to_json()

    # ---------------------------------------------------------------- stats

    async def _stats_loop(self) -> None:
        while True:
            t0 = time.monotonic()
            for rt in list(self.running.values()):
                if rt.state == "running":
                    try:
                        await self._sample(rt)
                    except Exception as e:  # noqa: BLE001
                        log.debug("stats for %s failed: %s", rt.name, e)
            await asyncio.sleep(max(0.1, STATS_INTERVAL - (time.monotonic() - t0)))

    async def _qdisc_stats(self, rt: LabRuntime) -> dict[tuple, dict]:
        out: dict[tuple, dict] = {}
        nss = {rt.iface_ns(n) for n in rt.plan.nodes}

        async def one(ns):
            try:
                raw = await rt.runner.tc(ns, "-s", "-j", "qdisc", "show", quiet=True)
                for q in json.loads(raw or "[]"):
                    if q.get("root"):
                        out[(ns, q.get("dev"))] = q
            except (CommandError, ValueError):
                pass
        await asyncio.gather(*(one(ns) for ns in nss))
        return out

    async def _sample(self, rt: LabRuntime) -> None:
        now = time.time()
        qs = await self._qdisc_stats(rt)
        sample: dict[str, Any] = {"t": now, "links": {}, "proxies": {}}
        for link_id, lp in rt.plan.links.items():
            entry = {}
            for direction, end in (("up", lp.a), ("down", lp.b)):
                q = qs.get((rt.iface_ns(end.node), end.name))
                if not q:
                    continue
                key = (link_id, direction)
                prev = rt.counters.get(key)
                cur = (now, q.get("bytes", 0), q.get("packets", 0), q.get("drops", 0))
                rt.counters[key] = cur
                d = {"bytes": cur[1], "packets": cur[2], "drops": cur[3],
                     "backlog": q.get("backlog", 0), "qlen": q.get("qlen", 0), "bps": 0, "pps": 0, "dps": 0}
                if prev and cur[1] >= prev[1] and cur[0] > prev[0]:
                    dt = cur[0] - prev[0]
                    d["bps"] = (cur[1] - prev[1]) * 8 / dt
                    d["pps"] = (cur[2] - prev[2]) / dt
                    d["dps"] = max(0, cur[3] - prev[3]) / dt
                entry[direction] = d
            sample["links"][link_id] = entry
        for name, ps in rt.proxies.items():
            if ps.impl:
                sample["proxies"][name] = ps.impl.stats.to_json()
        rt.stats.append(sample)
        self.bus.publish(f"lab:{rt.name}:stats", sample)

    # ---------------------------------------------------------------- captures

    async def capture_start(self, name: str, link_id: str, side: str = "a", bpf: str = "") -> Capture:
        rt = self._rt(name)
        if link_id not in rt.plan.links:
            raise EngineError(f"unknown link {link_id}")
        lp = rt.plan.links[link_id]
        end = lp.a if side == "a" else lp.b
        if rt.plan.nodes[end.node].kind == "switch":
            end, side = (lp.b, "b") if side == "a" else (lp.a, "a")
        cid = uuid.uuid4().hex[:10]
        path = self.run_dir / "captures" / f"{name}-{link_id}-{cid}.pcap"
        cap = Capture(cid, name, link_id, side, end.node, end.name, bpf, path, time.time())
        local_ip = str(end.ip.ip) if end.ip else None
        topic = f"capture:{cid}"

        def on_line(line: str):
            pkt = parse.tcpdump_line(line)
            if not pkt:
                return
            cap.packets += 1
            if "dir" not in pkt and local_ip and pkt.get("src"):
                pkt["dir"] = "out" if pkt["src"] == local_ip else "in"
            if len(cap.pending) < 2000:
                cap.pending.append(pkt)
            else:
                cap.dropped += 1

        def on_limit():
            self.event(rt, "warn", f"capture {cid} reached {cap.proc.max_bytes // 2**20} MiB and stopped; "
                                   "download it and start a new one")
            asyncio.get_running_loop().create_task(self.capture_stop(cid))

        cap.proc = PacketCapture(rt.iface_ns(end.node), end.name, path, bpf, on_line, on_limit)
        try:
            await cap.proc.start()
        except (OSError, ValueError) as e:
            await cap.proc.stop()
            raise EngineError(str(e)) from None

        async def flush():
            try:
                while True:
                    await asyncio.sleep(0.25)
                    if cap.pending or cap.dropped:
                        batch, cap.pending = cap.pending, []
                        self.bus.publish(topic, {"packets": batch, "dropped": cap.dropped, "total": cap.packets})
                        cap.dropped = 0
            except asyncio.CancelledError:
                pass
        cap.flusher = asyncio.create_task(flush())
        self.captures[cid] = cap
        mine = sorted((c for c in self.captures.values() if c.lab == name), key=lambda c: c.started)
        excess = len(mine) - KEEP_CAPTURES
        for old in mine:  # drop the oldest finished captures beyond the limit
            if excess <= 0:
                break
            if not (old.proc and old.proc.running):
                self.capture_delete(old.id)
                excess -= 1
        self.event(rt, "info", f"capture {cid} on {link_id} ({end.node}/{end.name})")
        self._changed(rt)
        return cap

    async def capture_stop(self, cid: str) -> Capture:
        cap = self.captures.get(cid)
        if not cap:
            raise EngineError(f"unknown capture {cid}")
        if cap.proc:
            await cap.proc.stop()
        if cap.flusher:
            cap.flusher.cancel()
        if (rt := self.running.get(cap.lab)) is not None:
            self._changed(rt)
        return cap

    def capture_delete(self, cid: str) -> None:
        cap = self.captures.pop(cid, None)
        if cap:
            cap.path.unlink(missing_ok=True)

    # ---------------------------------------------------------------- probes

    async def probe_start(self, name: str, kind: str, src: str, dst: str, options: dict | None = None) -> Probe:
        rt = self._rt(name)
        options = options or {}
        for n in (src, dst):
            if n not in rt.plan.nodes or rt.plan.nodes[n].kind == "switch":
                raise EngineError(f"invalid probe node {n}")
        dst_ip = rt.plan.node_ip(dst, toward=src)
        pid = uuid.uuid4().hex[:10]
        probe = Probe(pid, name, kind, src, dst, options, time.time())
        topic = f"probe:{pid}"
        root = User(0, 0, "root", "/root")

        def push(item):
            item["t"] = time.time()
            probe.results.append(item)
            self.bus.publish(topic, item)

        if kind == "ping":
            interval = max(0.01, float(options.get("interval", 0.2)))
            size = int(options.get("size", 56))
            argv = self.node_argv(rt, src, root, ["ping", "-n", "-O", "-i", str(interval), "-s", str(size),
                                                  "-W", "10", dst_ip])
            probe.procs.append(await stream_lines(argv, lambda line: (r := parse.ping_line(line)) and push(r)))
        elif kind == "iperf":
            port = 5201 + len(self.probes) % 100
            duration = int(options.get("duration", 10))
            server = await stream_lines(self.node_argv(rt, dst, root, ["iperf3", "-s", "-1", "-p", str(port)]),
                                        lambda line: None)
            probe.procs.append(server)
            await asyncio.sleep(0.4)
            argv = ["iperf3", "-c", dst_ip, "-p", str(port), "-t", str(duration), "-i", "1", "--forceflush"]
            if options.get("udp"):
                argv += ["-u", "-b", str(options.get("bitrate", "1M"))]
            if options.get("reverse"):
                argv.append("-R")
            if options.get("parallel"):
                argv += ["-P", str(int(options["parallel"]))]

            def on_line(line):
                if r := parse.iperf_line(line):
                    push(r)
                elif "error" in line.lower():
                    push({"error": line.strip()})
            probe.procs.append(await stream_lines(self.node_argv(rt, src, root, argv), on_line))
        else:
            raise EngineError(f"unknown probe kind {kind}")

        async def watch():
            await probe.procs[-1].wait()
            probe.running = False
            for p in probe.procs:
                await kill_tree(p)
            self.bus.publish(topic, {"done": True, "t": time.time()})
            self._changed(rt)
        probe.task = asyncio.create_task(watch())
        self.probes[pid] = probe
        mine = sorted((p for p in self.probes.values() if p.lab == name), key=lambda p: p.started)
        for old in mine[:-KEEP_PROBES]:
            if not old.running:
                self.probes.pop(old.id, None)
        self._changed(rt)
        return probe

    async def probe_stop(self, pid: str) -> Probe:
        probe = self.probes.get(pid)
        if not probe:
            raise EngineError(f"unknown probe {pid}")
        for p in probe.procs:
            await kill_tree(p)
        probe.running = False
        return probe

    # ---------------------------------------------------------------- inspection

    def _rt(self, name: str) -> LabRuntime:
        rt = self.running.get(name)
        if not rt:
            raise EngineError(f"lab {name} is not running")
        return rt

    def runtime(self, name: str) -> dict:
        rt = self._rt(name)
        return {
            "name": name, "state": rt.state, "started_at": rt.started_at, "error": rt.error,
            "plan": rt.plan.to_json(),
            "lab": rt.lab.model_dump(exclude_none=True),
            "links": {k: v.to_json() for k, v in rt.links.items()},
            "programs": {k: v.to_json() for k, v in rt.programs.items()},
            "proxies": {k: v.to_json() for k, v in rt.proxies.items()},
            "captures": [c.to_json() for c in self.captures.values() if c.lab == name],
            "probes": [p.to_json() for p in self.probes.values() if p.lab == name],
            "modified": self.labs.exists(name) and self.labs.text(name) != rt.source_text,
        }

    async def node_info(self, name: str, node: str) -> dict:
        rt = self._rt(name)
        if node not in rt.plan.nodes:
            raise EngineError(f"unknown node {node}")
        ns = rt.iface_ns(node)
        r = rt.runner
        info: dict[str, Any] = {"node": rt.plan.nodes[node].to_json()}
        if rt.plan.nodes[node].kind == "external":
            ifnames = {i.name for i in rt.plan.nodes[node].ifaces}
            info["addr"] = [a for a in await r.ip_json(None, "addr", "show") if a.get("ifname") in ifnames]
            info["routes"] = [x for x in await r.ip_json(None, "route", "show") if x.get("dev") in ifnames]
        else:
            info["addr"] = await r.ip_json(ns, "addr", "show")
            info["routes"] = await r.ip_json(ns, "route", "show")
        if rt.plan.nodes[node].kind != "switch" and ns:
            tcp = await r.nsexec(ns, "ss", "-tinHOp", check=False, quiet=True)
            info["tcp"] = [x for x in map(parse.ss_tcp_line, tcp.splitlines()) if x]
            lst = await r.nsexec(ns, "ss", "-tulnHp", check=False, quiet=True)
            info["listening"] = [x for x in map(parse.ss_listen_line, lst.splitlines()) if x]
        return info

    def history(self, name: str, since: float = 0) -> list[dict]:
        rt = self._rt(name)
        return [s for s in rt.stats if s["t"] > since]

    # ---------------------------------------------------------------- terminals

    async def open_terminal(self, name: str, node: str, as_root: bool, cols: int, rows: int,
                            on_output, on_exit) -> PtyProcess:
        rt = self._rt(name)
        if node not in rt.plan.nodes or rt.plan.nodes[node].kind == "switch":
            raise EngineError(f"cannot open a shell on {node}")
        user = User(0, 0, "root", "/root") if as_root else self.user
        rc = self.run_dir / f"bashrc-{uuid.uuid4().hex[:8]}"
        rc.write_text(
            '[ -f ~/.bashrc ] && . ~/.bashrc\n'
            f'PS1="\\[\\e[1;35m\\][{name}/{node}]\\[\\e[0m\\] \\u:\\w\\$ "\n'
            f'echo "netsim shell - lab {name}, node {node} ({rt.plan.nodes[node].primary_ip or "-"})"\n')
        os.chmod(rc, 0o644)
        argv = self.node_argv(rt, node, user, ["/bin/bash", "--rcfile", str(rc), "-i"])
        cwd = user.home if os.path.isdir(user.home) else "/"
        proc = PtyProcess(argv, self._proc_env(user, name, node), cwd=cwd, on_output=on_output,
                          on_exit=lambda c: (rc.unlink(missing_ok=True), on_exit(c)))
        await proc.start(cols, rows)
        return proc


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def _split_rule(rule: str) -> list[str]:
    return shlex.split(rule)


def _nsswitch_conf() -> str:
    """Host nsswitch with plain files+dns for hosts: systemd-resolved lives outside
    the namespace and would answer NOTFOUND for lab node names."""
    try:
        lines = Path("/etc/nsswitch.conf").read_text().splitlines()
    except OSError:
        lines = ["passwd: files", "group: files", "shadow: files"]
    lines = [line for line in lines if not line.lstrip().startswith("hosts:")]
    return "\n".join([*lines, "hosts: files dns"]) + "\n"


def _resolv_conf() -> str:
    for candidate in ("/etc/resolv.conf", "/run/systemd/resolve/resolv.conf"):
        try:
            text = Path(candidate).read_text()
        except OSError:
            continue
        servers = [line.split()[1] for line in text.splitlines()
                   if line.startswith("nameserver") and len(line.split()) > 1]
        public = [s for s in servers if not s.startswith("127.") and s != "::1"]
        if public:
            return "".join(f"nameserver {s}\n" for s in public[:3])
    return "nameserver 1.1.1.1\nnameserver 8.8.8.8\n"
