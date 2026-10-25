# SPDX-License-Identifier: LGPL-3.0-or-later
# Copyright (C) 2026 Eduardo Correia
from netsim import cli


def parse(argv):
    captured = {}
    original = cli.cmd_exec
    cli.cmd_exec = lambda args: captured.setdefault("args", args)
    try:
        cli.main(argv)
    finally:
        cli.cmd_exec = original
    return captured["args"]


def test_exec_root_before_and_after_positionals():
    a = parse(["exec", "lab", "node", "--root", "--", "id", "-u"])
    assert a.root and a.lab == "lab" and a.cmd == ["id", "-u"]
    b = parse(["exec", "--root", "lab", "node", "--", "ls", "--all"])
    assert b.root and b.cmd == ["ls", "--all"]
    c = parse(["exec", "lab", "node"])
    assert not c.root and c.cmd == []
