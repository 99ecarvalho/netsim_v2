"""Compile a Lab into a concrete plan: namespaces, interfaces, addresses, routes.

Pure computation, no system calls, so it can be previewed and unit tested.

Model:
  * every non-external node is a network namespace; the external node is the
    real host (root namespace);
  * every link is a veth pair; each end's egress is shaped (a->b on a's end);
  * a switch is a Linux bridge inside its own namespace; switches linked
    together form one L2 segment;
  * a segment (point-to-point link or switch group) gets one subnet;
  * routes are computed with BFS so every node reaches every segment, and a
    default route points toward the external node when there is one.
"""

from __future__ import annotations

import hashlib
import ipaddress
from collections import deque
from dataclasses import dataclass, field

from netsim.models import Lab

IPv4Net = ipaddress.IPv4Network
IPv4If = ipaddress.IPv4Interface

NS_PREFIX = "netsim-"
ATTACHED_PREFIX = "netsimc-"  # names for container namespaces we attach to but do not own
HOST_IF_PREFIX = "nsx"
CONTAINER_IF_PREFIX = "sim"


class PlanError(ValueError):
    pass


def lab_tag(lab_name: str) -> str:
    return hashlib.sha1(lab_name.encode()).hexdigest()[:5]


def ns_name(lab_name: str, node: str, kind: str = "host") -> str:
    return f"{ATTACHED_PREFIX if kind == 'container' else NS_PREFIX}{lab_name}-{node}"


@dataclass
class Iface:
    node: str
    name: str
    link_id: str
    side: str  # "a" or "b"
    peer: str
    ip: IPv4If | None = None
    mtu: int | None = None

    def to_json(self):
        return {"node": self.node, "name": self.name, "link": self.link_id, "side": self.side,
                "peer": self.peer, "ip": str(self.ip) if self.ip else None, "mtu": self.mtu}


@dataclass
class Route:
    dst: str  # CIDR or "default"
    via: str
    dev: str

    def to_json(self):
        return {"dst": self.dst, "via": self.via, "dev": self.dev}


@dataclass
class NodePlan:
    name: str
    kind: str
    ns: str | None  # None = root namespace
    ifaces: list[Iface] = field(default_factory=list)
    routes: list[Route] = field(default_factory=list)

    @property
    def primary_ip(self) -> str | None:
        for i in self.ifaces:
            if i.ip:
                return str(i.ip.ip)
        return None

    def to_json(self):
        return {"name": self.name, "kind": self.kind, "ns": self.ns, "ip": self.primary_ip,
                "ifaces": [i.to_json() for i in self.ifaces], "routes": [r.to_json() for r in self.routes]}


@dataclass
class LinkPlan:
    id: str
    a: Iface
    b: Iface
    segment: int

    def to_json(self):
        return {"id": self.id, "a": self.a.to_json(), "b": self.b.to_json(), "segment": self.segment}


@dataclass
class Segment:
    index: int
    subnet: IPv4Net
    members: list[tuple[str, Iface]]  # L3 nodes on this segment
    switches: list[str]

    def to_json(self):
        return {"index": self.index, "subnet": str(self.subnet), "switches": self.switches,
                "members": [{"node": n, "iface": i.name, "ip": str(i.ip.ip) if i.ip else None}
                            for n, i in self.members]}


@dataclass
class Plan:
    lab: str
    base: IPv4Net
    nodes: dict[str, NodePlan]
    links: dict[str, LinkPlan]
    segments: list[Segment]
    external: str | None
    internet: bool

    def hosts(self) -> dict[str, str]:
        return {n.name: n.primary_ip for n in self.nodes.values() if n.primary_ip and n.kind != "switch"}

    def node_ip(self, node: str, toward: str | None = None) -> str:
        """Address of `node`; if `toward` is given, the one on the segment closest to it."""
        n = self.nodes[node]
        if toward:
            for seg in self.segments:
                names = [m for m, _ in seg.members]
                if node in names and toward in names:
                    return str(dict(seg.members)[node].ip.ip)
        ip = n.primary_ip
        if not ip:
            raise PlanError(f"node {node} has no address")
        return ip

    def to_json(self):
        return {"lab": self.lab, "base": str(self.base), "external": self.external, "internet": self.internet,
                "nodes": {k: v.to_json() for k, v in self.nodes.items()},
                "links": {k: v.to_json() for k, v in self.links.items()},
                "segments": [s.to_json() for s in self.segments], "hosts": self.hosts()}


class _UF:
    def __init__(self):
        self.p: dict[str, str] = {}

    def find(self, x):
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        self.p[self.find(a)] = self.find(b)


def _parse_if(value: str, what: str) -> IPv4If:
    try:
        return IPv4If(value if "/" in value else value + "/24")
    except ValueError as e:
        raise PlanError(f"{what}: {e}") from None


def _parse_net(value: str, what: str) -> IPv4Net:
    try:
        return IPv4Net(value, strict=False)
    except ValueError as e:
        raise PlanError(f"{what}: {e}") from None


def compile_lab(lab: Lab, name: str, base: str | IPv4Net) -> Plan:
    base = _parse_net(str(base), "lab subnet")
    kinds = {n: node.kind for n, node in lab.nodes.items()}
    external = next((n for n, k in kinds.items() if k == "external"), None)
    tag = lab_tag(name)

    nodes = {n: NodePlan(n, k, None if k == "external" else ns_name(name, n, k)) for n, k in kinds.items()}
    counters: dict[str, int] = {n: 0 for n in nodes}

    def new_iface(node: str, link_id: str, side: str, peer: str, mtu: int | None) -> Iface:
        i = counters[node]
        counters[node] += 1
        if kinds[node] == "external":
            ifname = f"{HOST_IF_PREFIX}{tag}{i}"
        elif kinds[node] == "switch":
            ifname = f"p{i}"
        elif kinds[node] == "container":
            ifname = f"{CONTAINER_IF_PREFIX}{i}"  # the container already has its own eth0
        else:
            ifname = f"eth{i}"
        iface = Iface(node, ifname, link_id, side, peer, mtu=mtu)
        nodes[node].ifaces.append(iface)
        return iface

    # ---- interfaces and L2 segments
    uf = _UF()
    for link in lab.links:
        if kinds[link.a] == "switch" and kinds[link.b] == "switch":
            uf.union(link.a, link.b)

    links: dict[str, LinkPlan] = {}
    seg_keys: list[str] = []  # segment key: switch-group root, or "link:<id>"
    seg_members: dict[str, list[tuple[str, Iface, str | None]]] = {}  # key -> (node, iface, explicit ip)
    seg_subnet_hint: dict[str, str] = {}

    for link in lab.links:
        a_if = new_iface(link.a, link.id, "a", link.b, link.mtu)
        b_if = new_iface(link.b, link.id, "b", link.a, link.mtu)
        sw_a, sw_b = kinds[link.a] == "switch", kinds[link.b] == "switch"
        if sw_a or sw_b:
            key = "sw:" + uf.find(link.a if sw_a else link.b)
        else:
            key = "link:" + link.id
        if key not in seg_members:
            seg_keys.append(key)
            seg_members[key] = []
        if not sw_a:
            seg_members[key].append((link.a, a_if, link.ip_a))
        if not sw_b:
            seg_members[key].append((link.b, b_if, link.ip_b))
        if link.subnet:
            seg_subnet_hint.setdefault(key, link.subnet)
        links[link.id] = LinkPlan(link.id, a_if, b_if, -1)

    for sw, node in lab.nodes.items():
        if node.kind == "switch" and node.subnet:
            key = "sw:" + uf.find(sw)
            if key in seg_subnet_hint and seg_subnet_hint[key] != node.subnet:
                raise PlanError(f"switch {sw}: conflicting subnets in the same L2 segment")
            seg_subnet_hint[key] = node.subnet

    # ---- subnets
    explicit_nets: list[IPv4Net] = []
    seg_nets: dict[str, IPv4Net] = {}
    for key in seg_keys:
        hint = seg_subnet_hint.get(key)
        if not hint:
            ips = [ip for _, _, ip in seg_members[key] if ip]
            hint = str(_parse_if(ips[0], key).network) if ips else None
        if hint:
            net = _parse_net(hint, f"subnet of {key}")
            for other in explicit_nets:
                if net.overlaps(other):
                    raise PlanError(f"subnet {net} overlaps {other}")
            explicit_nets.append(net)
            seg_nets[key] = net

    prefix = 24 if base.prefixlen <= 24 else min(30, base.prefixlen + 2)
    auto = (s for s in base.subnets(new_prefix=prefix) if not any(s.overlaps(e) for e in explicit_nets))
    for key in seg_keys:
        if key not in seg_nets:
            try:
                seg_nets[key] = next(auto)
            except StopIteration:
                raise PlanError(f"lab subnet {base} too small for {len(seg_keys)} segments") from None

    # ---- addresses
    segments: list[Segment] = []
    for idx, key in enumerate(seg_keys):
        net = seg_nets[key]
        taken: set[ipaddress.IPv4Address] = set()
        members = seg_members[key]
        for node, iface, ip in members:
            if ip:
                addr = _parse_if(ip if "/" in ip else f"{ip}/{net.prefixlen}", f"{node} address")
                if addr.ip not in net:
                    raise PlanError(f"{node}: {addr} is not inside segment subnet {net}")
                if addr.ip in taken:
                    raise PlanError(f"duplicate address {addr.ip}")
                iface.ip = IPv4If(f"{addr.ip}/{net.prefixlen}")
                taken.add(addr.ip)
        free = (h for h in net.hosts() if h not in taken)
        for node, iface, ip in members:
            if not iface.ip:
                try:
                    iface.ip = IPv4If(f"{next(free)}/{net.prefixlen}")
                except StopIteration:
                    raise PlanError(f"subnet {net} has no room for {node}") from None
        switches = sorted({n for n in lab.nodes if kinds[n] == "switch" and "sw:" + uf.find(n) == key})
        segments.append(Segment(idx, net, [(n, i) for n, i, _ in members], switches))
        for lp in links.values():
            if lp.a.ip and lp.a.ip.ip in net or lp.b.ip and lp.b.ip.ip in net:
                lp.segment = idx
    for lp in links.values():  # switch-to-switch links carry no address; find their segment by switch
        if lp.segment < 0:
            for seg in segments:
                if lp.a.node in seg.switches:
                    lp.segment = seg.index

    # ---- routes (BFS over L3 adjacency)
    adj: dict[str, list[tuple[str, Iface, Iface, int]]] = {n: [] for n in nodes if kinds[n] != "switch"}
    for seg in segments:
        for n1, i1 in seg.members:
            for n2, i2 in seg.members:
                if n1 != n2:
                    adj[n1].append((n2, i1, i2, seg.index))

    seg_of_node: dict[str, set[int]] = {n: set() for n in adj}
    for seg in segments:
        for n, _ in seg.members:
            seg_of_node[n].add(seg.index)

    internet = bool(external and lab.nodes[external].internet)
    for src in adj:
        first_hop: dict[str, tuple[str, str]] = {}  # node -> (via ip, dev)
        dist = {src: 0}
        q = deque([src])
        while q:
            cur = q.popleft()
            for nxt, my_if, their_if, _ in adj[cur]:
                if nxt in dist:
                    continue
                dist[nxt] = dist[cur] + 1
                first_hop[nxt] = (str(their_if.ip.ip), my_if.name) if cur == src else first_hop[cur]
                q.append(nxt)
        routes: list[Route] = []
        for seg in segments:
            if seg.index in seg_of_node[src]:
                continue
            candidates = [(dist[n], n) for n, _ in seg.members if n in dist]
            if not candidates:
                continue
            _, nearest = min(candidates)
            via, dev = first_hop[nearest]
            routes.append(Route(str(seg.subnet), via, dev))
        # containers keep their own default route (docker bridge); they only learn the lab subnets
        if external and src != external and external in first_hop and kinds[src] != "container":
            via, dev = first_hop[external]
            routes.append(Route("default", via, dev))
        nodes[src].routes = routes

    return Plan(name, base, nodes, links, segments, external, internet)
