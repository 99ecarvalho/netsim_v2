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


def test_doctor_netem_check_without_tools(monkeypatch, tmp_path):
    monkeypatch.setattr(cli.shutil, "which", lambda name: None)  # no modinfo, nothing on PATH
    real_exists = cli.Path.exists
    monkeypatch.setattr(cli.Path, "exists", lambda self: False if str(self) == "/sys/module/sch_netem" else real_exists(self))
    ok, how = cli._netem_status()  # must not raise FileNotFoundError
    assert isinstance(ok, bool) and how
