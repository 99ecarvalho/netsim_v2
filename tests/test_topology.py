# SPDX-License-Identifier: LGPL-3.0-or-later
# Copyright (C) 2026 Eduardo Correia
import pytest

from netsim.models import Lab
from netsim.topology import PlanError, compile_lab


def lab(**kw):
    return Lab.model_validate(kw)


def test_point_to_point():
    p = compile_lab(lab(nodes={"client": {}, "server": {}}, links=[{"a": "client", "b": "server"}]),
                    "t", "10.201.0.0/16")
    assert p.nodes["client"].primary_ip == "10.201.0.1"
    assert p.nodes["server"].primary_ip == "10.201.0.2"
    assert p.nodes["client"].routes == []
    assert p.links["client--server"].a.name == "eth0"


def test_lan_router_wan_routes_and_default():
    p = compile_lab(lab(nodes={
        "pc1": {}, "pc2": {}, "lan": {"kind": "switch"}, "gw": {"kind": "router"},
        "cloud": {}, "host": {"kind": "external", "internet": True}},
        links=[{"a": "pc1", "b": "lan"}, {"a": "pc2", "b": "lan"}, {"a": "gw", "b": "lan"},
               {"a": "gw", "b": "cloud"}, {"a": "gw", "b": "host"}]), "t", "10.201.0.0/16")
    assert len(p.segments) == 3
    lan = p.segments[0]
    assert sorted(n for n, _ in lan.members) == ["gw", "pc1", "pc2"]
    pc1 = {r.dst: r for r in p.nodes["pc1"].routes}
    gw_lan_ip = p.node_ip("gw", toward="pc1")
    assert pc1["10.201.1.0/24"].via == gw_lan_ip
    assert pc1["default"].via == gw_lan_ip
    host = {r.dst: r for r in p.nodes["host"].routes}
    assert host["10.201.0.0/24"].dev.startswith("nsx")
    assert "default" not in host
    assert p.internet


def test_explicit_addresses_and_overlap():
    p = compile_lab(lab(nodes={"a": {}, "b": {}}, links=[{"a": "a", "b": "b", "ip_a": "192.168.7.10/24"}]),
                    "t", "10.201.0.0/16")
    assert p.nodes["a"].primary_ip == "192.168.7.10"
    assert p.nodes["b"].primary_ip == "192.168.7.1"
    with pytest.raises(PlanError):
        compile_lab(lab(nodes={"a": {}, "b": {}, "c": {}},
                        links=[{"a": "a", "b": "b", "subnet": "10.0.0.0/16"},
                               {"a": "b", "b": "c", "subnet": "10.0.5.0/24"}]), "t", "10.201.0.0/16")


def test_switch_chain_is_one_segment():
    p = compile_lab(lab(nodes={"a": {}, "b": {}, "s1": {"kind": "switch"}, "s2": {"kind": "switch"}},
                        links=[{"a": "a", "b": "s1"}, {"a": "s1", "b": "s2"}, {"a": "b", "b": "s2"}]),
                    "t", "10.201.0.0/16")
    assert len(p.segments) == 1
    assert p.links["s1--s2"].segment == 0


def test_validation_errors():
    with pytest.raises(ValueError):
        lab(nodes={"a": {}}, links=[{"a": "a", "b": "zz"}])
    with pytest.raises(ValueError):
        lab(nodes={"a": {}, "b": {}}, proxies=[{"name": "p", "listen": "6000", "target": "5000"}])
    ok = lab(nodes={"a": {}, "b": {}}, links=[{"a": "a", "b": "b"}],
             proxies=[{"name": "p", "listen": "6000", "target": "127.0.0.1:5000", "via": ["a", "b"]}])
    assert ok.proxies[0].endpoints(ok.nodes) == ("a", "b")


def test_container_node_keeps_its_default_route():
    p = compile_lab(lab(nodes={"c": {}, "api": {"kind": "container", "container": "web"},
                               "host": {"kind": "external"}},
                        links=[{"a": "c", "b": "api"}, {"a": "api", "b": "host"}]), "t", "10.201.0.0/16")
    assert p.nodes["api"].ns == "netsimc-t@api"
    from netsim.topology import lab_tag
    assert p.nodes["api"].ifaces[0].name == f"sim{lab_tag('t')}0"
    assert len(p.nodes["api"].ifaces[0].name) <= 15
    assert all(r.dst != "default" for r in p.nodes["api"].routes)
    assert any(r.dst == "default" for r in p.nodes["c"].routes)
    with pytest.raises(ValueError):
        lab(nodes={"api": {"kind": "container"}})


def test_namespace_names_are_unambiguous():
    from netsim.topology import ns_lab, ns_name
    assert ns_name("foo-bar", "baz") != ns_name("foo", "bar-baz")
    assert ns_lab(ns_name("foo-bar", "baz")) == "foo-bar"
    assert ns_lab(ns_name("lab", "api", "container")) == "lab"
    assert ns_lab("something-else") is None
