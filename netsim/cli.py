# SPDX-License-Identifier: LGPL-3.0-or-later
# Copyright (C) 2026 Eduardo Correia
"""Command line interface. Most commands talk to the daemon started by `netsim serve`."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

from netsim import __version__
from netsim.config import DEFAULT_PORT, RUN_DIR, home_dir

ENDPOINT_FILE = RUN_DIR / "endpoint"


class CliError(Exception):
    pass


# ------------------------------------------------------------------ helpers

def elevate(argv: list[str], preserve_env: bool = False) -> None:
    """Re-run this command through sudo (keeps the user's PATH for programs)."""
    if os.geteuid() == 0:
        return
    if not shutil.which("sudo"):
        raise CliError("this command needs root and sudo is not available")
    # options go right after the subcommand so they never end up inside `exec -- CMD`
    cmd = ["sudo", *(["-E"] if preserve_env else []), sys.executable, "-m", "netsim", argv[0],
           "--user-path", os.environ.get("PATH", ""), *argv[1:]]
    os.execvp("sudo", cmd)


def endpoint() -> tuple[str, str]:
    url = os.environ.get("NETSIM_URL")
    if not url:
        try:
            url = ENDPOINT_FILE.read_text().strip()
        except OSError:
            url = f"http://127.0.0.1:{DEFAULT_PORT}"
    token = os.environ.get("NETSIM_TOKEN")
    if not token:
        try:
            token = (RUN_DIR / "token").read_text().strip()
        except PermissionError:
            raise CliError(f"cannot read {RUN_DIR / 'token'} (started by another user?)") from None
        except OSError:
            raise CliError("netsim daemon is not running - start it with `netsim serve`") from None
    return url, token


class Api:
    def __init__(self):
        import httpx
        self.url, token = endpoint()
        self.client = httpx.Client(base_url=self.url, headers={"Authorization": f"Bearer {token}"}, timeout=60)

    def request(self, method: str, path: str, **kw):
        import httpx
        try:
            r = self.client.request(method, path, **kw)
        except httpx.ConnectError:
            raise CliError(f"cannot reach netsim at {self.url} - is `netsim serve` running?") from None
        if r.status_code >= 400:
            try:
                detail = r.json().get("detail", r.text)
            except ValueError:
                detail = r.text
            raise CliError(detail)
        return r.json() if r.content else None

    def get(self, path, **kw):
        return self.request("GET", path, **kw)

    def post(self, path, body=None):
        return self.request("POST", path, json=body)


def fmt_bps(v: float) -> str:
    for div, unit in ((1e9, "G"), (1e6, "M"), (1e3, "k")):
        if v >= div:
            return f"{v / div:5.1f} {unit}bit/s"
    return f"{v:5.0f}  bit/s"


def table(rows: list[list[str]], header: list[str]) -> str:
    widths = [max(len(str(x)) for x in col) for col in zip(header, *rows)]
    lines = ["  ".join(str(h).ljust(w) for h, w in zip(header, widths)).rstrip()]
    lines += ["  ".join(str(c).ljust(w) for c, w in zip(r, widths)).rstrip() for r in rows]
    return "\n".join(lines)


# ------------------------------------------------------------------ commands

def cmd_serve(args) -> None:
    elevate(sys.argv[1:])
    if args.user:
        os.environ["NETSIM_USER"] = args.user
    import asyncio
    import logging

    import uvicorn

    from netsim.server import build

    from netsim.config import InstanceRunning, acquire_instance_lock

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s", datefmt="%H:%M:%S")
    # order matters: lock, then port, and only then anything with side effects
    try:
        lock = acquire_instance_lock(RUN_DIR)  # noqa: F841 - held for the life of the process
    except InstanceRunning as e:
        raise CliError(f"{e}. Open it with `netsim ui`, or stop it first.") from None
    sock = _bind(args.host, args.port)
    loopback = args.host in ("127.0.0.1", "localhost", "::1")
    # bound to a real interface: Host header can be anything, the token still protects the API
    app, token = build(user_path=args.user_path or None, check_host=loopback)
    shown = "127.0.0.1" if args.host in ("0.0.0.0", "::") else args.host
    url = f"http://{shown}:{args.port}"
    ENDPOINT_FILE.write_text(url + "\n")
    print(f"netsim {__version__}  profiles+labs in {home_dir()}", flush=True)
    if sys.stdout.isatty():
        print(f"web UI: {url}/?token={token}", flush=True)
    else:
        # never write the token where others may read it (log files, journald)
        print(f"web UI: {url}  (run `netsim ui` for the login link)", flush=True)
    if args.open:
        _open_browser(f"{url}/?token={token}")
    config = uvicorn.Config(app, log_level="warning", ws_ping_interval=20)
    server = uvicorn.Server(config)
    try:
        asyncio.run(server.serve(sockets=[sock]))
    except KeyboardInterrupt:
        pass


def _bind(host: str, port: int):
    import socket
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((host, port))
    except OSError as e:
        sock.close()
        raise CliError(f"cannot listen on {host}:{port}: {e.strerror}") from None
    sock.listen(128)
    sock.set_inheritable(True)
    return sock


def _open_browser(url: str) -> None:
    user = os.environ.get("SUDO_USER")
    opener = shutil.which("xdg-open") or shutil.which("open")
    if not opener:
        return
    cmd = [opener, url]
    if user and os.geteuid() == 0:
        cmd = ["sudo", "-u", user, "--preserve-env=DISPLAY,WAYLAND_DISPLAY,XDG_RUNTIME_DIR,DBUS_SESSION_BUS_ADDRESS",
               *cmd]
    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


def cmd_ui(args) -> None:
    url, token = endpoint()
    full = f"{url}/?token={token}"
    print(full)
    if not args.print_only:
        _open_browser(full)


def cmd_labs(args) -> None:
    labs = Api().get("/api/labs")
    rows = [[lab["name"], lab["state"] + (" (modified)" if lab.get("modified") else ""),
             "invalid" if lab.get("invalid") else "", (lab.get("description") or "").strip()[:60]]
            for lab in labs]
    print(table(rows, ["LAB", "STATE", "", "DESCRIPTION"]))


def cmd_profiles(args) -> None:
    profiles = Api().get("/api/profiles") if not args.offline else _offline_profiles()
    rows = []
    for p in profiles:
        if args.category and p.get("category") != args.category:
            continue
        s = p.get("summary") or {}
        tl = f"timeline({p['timeline']})" if p.get("timeline") else ""
        rows.append([p["name"], p.get("category") or "", s.get("up", p.get("error") or ""), s.get("down", ""), tl])
    print(table(rows, ["PROFILE", "CATEGORY", "UP (a->b)", "DOWN (b->a)", ""]))


def _offline_profiles() -> list[dict]:
    from netsim.models import Link
    from netsim.shaping import LinkShape, flatten_profile, netem
    from netsim.store import ProfileStore
    store = ProfileStore(home_dir() / "profiles")
    out = []
    for name in store.names():
        try:
            flat = flatten_profile(name, store.get)
            shape = LinkShape(flat, Link(a="a", b="b"))
            out.append({"name": name, "category": flat.category,
                        "timeline": len(flat.timeline.steps) if flat.timeline else 0,
                        "summary": {d: netem(shape.effective(d)).summary for d in ("up", "down")}})
        except Exception as e:  # noqa: BLE001
            out.append({"name": name, "error": str(e)})
    return out


def cmd_profile(args) -> None:
    api = Api()
    p = api.get(f"/api/profiles/{args.name}")
    print(p["text"].rstrip())
    v = api.post("/api/profiles/validate", {"text": p["text"], "name": args.name})
    if not v["ok"]:
        print(f"\nerror: {v['error']}")
        return
    print()
    for d in ("up", "down"):
        print(f"# {d}: {v['preview'][d]['summary']}\n{v['preview'][d]['tc']}")
    for s in v["steps"]:
        print(f"# step {s['label'] or ''} ({s['duration']}): up {s['up']['summary']} | down {s['down']['summary']}")


def cmd_up(args) -> None:
    rt = Api().post(f"/api/labs/{args.lab}/up")
    _print_runtime(rt)


def cmd_down(args) -> None:
    api = Api()
    names = [lab["name"] for lab in api.get("/api/labs") if lab["state"] == "running"] if args.all else [args.lab]
    if not names or names == [None]:
        raise CliError("give a lab name or --all")
    for name in names:
        api.post(f"/api/labs/{name}/down")
        print(f"{name}: stopped")


def cmd_restart(args) -> None:
    _print_runtime(Api().post(f"/api/labs/{args.lab}/restart"))


def cmd_status(args) -> None:
    api = Api()
    if not args.lab:
        running = [lab for lab in api.get("/api/labs") if lab["state"] != "stopped"]
        if not running:
            print("no labs running")
        for lab in running:
            print(f"== {lab['name']} ({lab['state']})")
            if lab["state"] == "running":
                _print_runtime(api.get(f"/api/labs/{lab['name']}/runtime"), brief=True)
        return
    _print_runtime(api.get(f"/api/labs/{args.lab}/runtime"))


def _print_runtime(rt: dict, brief: bool = False) -> None:
    plan = rt["plan"]
    rows = [[n["name"], n["kind"], ", ".join(i["ip"] for i in n["ifaces"] if i["ip"]) or "-", n["ns"] or "(host)"]
            for n in plan["nodes"].values()]
    print(table(rows, ["NODE", "KIND", "ADDRESSES", "NAMESPACE"]))
    print()
    rows = []
    for lid, ls in rt["links"].items():
        step = f"[{ls['step_label']}]" if ls.get("step_label") else ""
        rows.append([lid, ls.get("profile") or "-", step, (ls["up"] or {}).get("summary", ""),
                     (ls["down"] or {}).get("summary", "")])
    print(table(rows, ["LINK", "PROFILE", "", "UP (a->b)", "DOWN (b->a)"]))
    if brief:
        print()
        return
    if rt["programs"]:
        print()
        rows = [[p["name"], p["node"], p["status"] + (f" ({p['exit_code']})" if p["exit_code"] is not None else ""),
                 p["cmd"][:60]] for p in rt["programs"].values()]
        print(table(rows, ["PROGRAM", "NODE", "STATUS", "COMMAND"]))
    if rt["proxies"]:
        print()
        rows = [[p["name"], p["proto"], p["route"] or "", p["error"] or ("up" if p["running"] else "down")]
                for p in rt["proxies"].values()]
        print(table(rows, ["PROXY", "PROTO", "ROUTE", "STATE"]))


def _params_from_args(args) -> dict:
    params = {}
    for key in ("delay", "jitter", "loss", "rate", "corrupt", "duplicate", "reorder", "queue", "distribution"):
        v = getattr(args, key, None)
        if v is not None:
            params[key] = v
    return params


def cmd_shape(args) -> None:
    body: dict = {"reset": args.reset}
    if args.profile is not None:
        body["set_profile"] = True
        body["profile"] = args.profile or None
    params = _params_from_args(args)
    if params:
        body["params"] = {args.direction: params}
    ls = Api().request("PATCH", f"/api/labs/{args.lab}/links/{args.link}", json=body)
    print(f"up:   {ls['up']['summary']}\ndown: {ls['down']['summary']}")


def cmd_cut(args, blackhole: bool) -> None:
    body = {"params": {"both": {"blackhole": True if blackhole else None}}}
    ls = Api().request("PATCH", f"/api/labs/{args.lab}/links/{args.link}", json=body)
    print(f"{args.link}: {ls['up']['summary']}")


def cmd_exec(args) -> None:
    ns = f"netsim-{args.lab}-{args.node}"
    external = False
    try:
        rt = Api().get(f"/api/labs/{args.lab}/runtime")
        node = rt["plan"]["nodes"].get(args.node)
        if not node:
            raise CliError(f"lab {args.lab} has no node {args.node}")
        if node["kind"] == "switch":
            raise CliError("cannot run programs on a switch")
        external = node["kind"] == "external"
    except CliError as e:
        if "not running" in str(e) or not Path(f"/run/netns/{ns}").exists():
            raise
    cmd = args.cmd or [os.environ.get("SHELL", "/bin/bash")]
    if external:
        os.execvp(cmd[0], cmd)
    elevate(["exec", args.lab, args.node, *(["--root"] if args.root else []), "--", *args.cmd],
            preserve_env=True)
    uid = int(os.environ.get("SUDO_UID", 0))
    gid = int(os.environ.get("SUDO_GID", 0))
    env = dict(os.environ)
    if args.user_path:
        env["PATH"] = args.user_path
    env.update(NETSIM_LAB=args.lab, NETSIM_NODE=args.node)
    argv = ["ip", "netns", "exec", ns]
    if uid and not args.root:
        argv += ["setpriv", f"--reuid={uid}", f"--regid={gid}", "--init-groups", "--"]
        for k in ("SUDO_UID", "SUDO_GID", "SUDO_USER", "SUDO_COMMAND"):
            env.pop(k, None)
    os.execvpe("ip", [*argv, *cmd], env)


def cmd_logs(args) -> None:
    api = Api()
    for entry in api.get(f"/api/labs/{args.lab}/programs/{args.program}/logs"):
        print(entry["line"])
    if args.follow:
        _follow([f"program:{args.lab}:{args.program}"], lambda d: print(d["line"], flush=True))


def _follow(topics: list[str], handle) -> None:
    import asyncio

    import websockets

    url, token = endpoint()
    ws_url = url.replace("http", "ws", 1) + f"/api/ws?token={token}"

    async def run():
        async with websockets.connect(ws_url) as ws:
            await ws.send(json.dumps({"op": "sub", "topics": topics}))
            async for raw in ws:
                handle(json.loads(raw)["data"])
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass


def _parse_pipe(spec: str) -> tuple[str, str]:
    parts = spec.split(":")
    if len(parts) == 2:
        return f"127.0.0.1:{parts[0]}", f"127.0.0.1:{parts[1]}"
    if len(parts) == 3:
        return f"127.0.0.1:{parts[0]}", f"{parts[1]}:{parts[2]}"
    if len(parts) == 4:
        return f"{parts[0]}:{parts[1]}", f"{parts[2]}:{parts[3]}"
    raise CliError("pipe spec is LISTEN_PORT:TARGET_PORT, LISTEN_PORT:HOST:PORT or ADDR:PORT:HOST:PORT")


def cmd_pipe(args) -> None:
    import yaml

    listen, target = _parse_pipe(args.spec)
    name = args.name or f"pipe-{listen.rsplit(':', 1)[1]}"
    link = {"a": "client", "b": "server", "profile": args.profile, **_params_from_args(args)}
    lab = {"description": f"netsim pipe {listen} -> {target}",
           "nodes": {"client": {"icon": "laptop"}, "server": {"icon": "server"}},
           "links": [link],
           "proxies": [{"name": "pipe", "proto": "udp" if args.udp else "tcp", "listen": listen,
                        "target": target, "via": ["client", "server"]}]}
    api = Api()
    rt = api.post("/api/labs/ephemeral", {"name": name, "text": yaml.safe_dump(lab, sort_keys=False)})
    ls = rt["links"]["client--server"]
    proxy = rt["proxies"]["pipe"]
    if proxy["error"]:
        api.post(f"/api/labs/{name}/down")
        raise CliError(proxy["error"])
    print(f"{listen} -> [{args.profile}] -> {target}   (lab {name}, Ctrl-C to stop)")
    print(f"  client->server: {ls['up']['summary']}\n  server->client: {ls['down']['summary']}")

    stop = False

    def on_sigint(*_):
        nonlocal stop
        stop = True
    signal.signal(signal.SIGINT, on_sigint)
    signal.signal(signal.SIGTERM, on_sigint)
    since = 0.0
    tty = sys.stdout.isatty()
    try:
        while not stop:
            time.sleep(1)
            try:
                samples = api.get(f"/api/labs/{name}/history", params={"since": since})
            except CliError:
                break
            if not samples:
                continue
            s = samples[-1]
            since = s["t"]
            link = s["links"].get("client--server", {})
            up, down = link.get("up", {}), link.get("down", {})
            px = s["proxies"].get("pipe", {})
            line = (f"  up {fmt_bps(up.get('bps', 0))}  down {fmt_bps(down.get('bps', 0))}  "
                    f"drops {up.get('drops', 0) + down.get('drops', 0):6d}  conns {px.get('active', 0)}/"
                    f"{px.get('total', 0)}")
            print(("\r" + line) if tty else line, end="" if tty else "\n", flush=True)
    finally:
        print()
        try:
            api.post(f"/api/labs/{name}/down")
        except CliError:
            pass


def _forward_specs(values: list[str], proto: str) -> list[dict]:
    out = []
    for v in values or []:
        inner, _, outer = v.partition(":")
        if not inner.isdigit() or (outer and not outer.isdigit()):
            raise CliError(f"--forward takes PORT or PORT:HOSTPORT, got {v!r}")
        out.append({"name": f"fwd-{proto}-{inner}", "proto": proto, "listen": f"127.0.0.1:{inner}",
                    "listen_node": "app", "target": f"127.0.0.1:{outer or inner}", "via": ["app", "host"]})
    return out


def _local_proxy_ports() -> list[str]:
    """Ports of http(s) proxies on localhost: inside a node localhost is the node itself."""
    from urllib.parse import urlparse
    ports = []
    for var in ("http_proxy", "https_proxy", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        url = os.environ.get(var)
        if not url:
            continue
        u = urlparse(url if "://" in url else f"http://{url}")
        if u.hostname in ("localhost", "127.0.0.1") and u.port and str(u.port) not in ports:
            ports.append(str(u.port))
    return ports


def cmd_run(args) -> None:
    """Run CMD in a throwaway node whose only way out is a shaped link to this machine."""
    import yaml

    if not args.cmd:
        raise CliError("give a command: netsim run -p PROFILE -- CMD [ARGS...]")
    name = args.name or f"run-{os.getpid()}"
    forwards = list(args.forward or [])
    proxy_ports = _local_proxy_ports()
    forwards += [p for p in proxy_ports if p not in {f.split(":")[0] for f in forwards}]
    link = {"a": "app", "b": "host", "profile": args.profile, **_params_from_args(args)}
    lab = {"description": f"netsim run: {' '.join(args.cmd)[:80]}",
           "nodes": {"app": {"icon": "laptop"}, "host": {"kind": "external", "internet": not args.no_internet}},
           "links": [link],
           "proxies": _forward_specs(forwards, "tcp") + _forward_specs(args.forward_udp, "udp")}
    api = Api()
    rt = api.post("/api/labs/ephemeral", {"name": name, "text": yaml.safe_dump(lab, sort_keys=False)})
    code = 1
    try:
        for px in rt["proxies"].values():
            if px["error"]:
                raise CliError(f"forward {px['listen']}: {px['error']}")
        host_ip = rt["plan"]["nodes"]["host"]["ip"]
        if not args.quiet:
            ls = rt["links"]["app--host"]
            print(f"netsim: lab {name} · profile {args.profile} · this machine is 'host' ({host_ip})\n"
                  f"netsim:   out {ls['up']['summary']}\nnetsim:   in  {ls['down']['summary']}", file=sys.stderr)
            for px in rt["proxies"].values():
                port = px["listen"].rsplit(":", 1)[1]
                why = " (your http proxy)" if port in proxy_ports else ""
                print(f"netsim:   localhost:{port} -> host {px['target']} ({px['proto']}){why}", file=sys.stderr)
        argv = [sys.executable, "-m", "netsim", "exec", "--user-path", os.environ.get("PATH", ""), name, "app",
                "--", *args.cmd]
        if os.geteuid() != 0:
            argv = ["sudo", "-E", *argv]
        # the child gets Ctrl-C from the terminal; we only clean up after it
        old = signal.signal(signal.SIGINT, signal.SIG_IGN)
        try:
            code = subprocess.call(argv)
        finally:
            signal.signal(signal.SIGINT, old)
    finally:
        try:
            api.post(f"/api/labs/{name}/down")
        except CliError:
            pass
    sys.exit(code)


def cmd_validate(args) -> None:
    from netsim.models import Lab, Profile
    from netsim.store import StoreError, parse_yaml
    path = Path(args.file)
    text = path.read_text()
    kind = args.kind or ("lab" if "nodes:" in text else "profile")
    try:
        obj = parse_yaml(text, Lab if kind == "lab" else Profile, path.stem)
    except StoreError as e:
        raise CliError(f"{path}: {e}") from None
    if kind == "lab":
        from netsim.topology import compile_lab
        plan = compile_lab(obj, path.stem, obj.subnet or "10.201.0.0/16")
        for n in plan.nodes.values():
            print(f"{n.name:16} {n.kind:9} {', '.join(str(i.ip) for i in n.ifaces if i.ip)}")
    print(f"{path}: ok ({kind})")


def cmd_cleanup(args) -> None:
    removed = Api().post("/api/cleanup")["removed"]
    print("\n".join(removed) if removed else "nothing to clean")


# ------------------------------------------------------------------ parser

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="netsim", description="Virtual network labs with traffic shaping.")
    p.add_argument("--version", action="version", version=f"netsim {__version__}")
    sub = p.add_subparsers(dest="command", metavar="COMMAND")

    def add(name, fn, help_, aliases=()):
        sp = sub.add_parser(name, help=help_, description=help_, aliases=list(aliases))
        sp.set_defaults(fn=fn)
        sp.add_argument("--user-path", help=argparse.SUPPRESS)
        return sp

    sp = add("serve", cmd_serve, "start the daemon and web UI (needs root, uses sudo)")
    sp.add_argument("--host", default="127.0.0.1")
    sp.add_argument("--port", type=int, default=DEFAULT_PORT)
    sp.add_argument("--open", action="store_true", help="open the web UI in a browser")
    sp.add_argument("--user", help="run lab programs and own files as this user (default: the sudo user)")
    sp.add_argument("-v", "--verbose", action="store_true")

    sp = add("ui", cmd_ui, "open the web UI of the running daemon")
    sp.add_argument("--print-only", action="store_true", help="only print the URL")

    add("labs", cmd_labs, "list labs", aliases=["ls"])

    sp = add("profiles", cmd_profiles, "list link profiles")
    sp.add_argument("-c", "--category")
    sp.add_argument("--offline", action="store_true", help="read profiles directly, without the daemon")

    sp = add("profile", cmd_profile, "show a profile and the netem commands it produces")
    sp.add_argument("name")

    sp = add("up", cmd_up, "start a lab")
    sp.add_argument("lab")
    sp = add("down", cmd_down, "stop a lab")
    sp.add_argument("lab", nargs="?")
    sp.add_argument("--all", action="store_true")
    sp = add("restart", cmd_restart, "stop and start a lab (picks up file changes)")
    sp.add_argument("lab")
    sp = add("status", cmd_status, "show running labs, or one lab in detail")
    sp.add_argument("lab", nargs="?")

    def shaping_args(sp):
        sp.add_argument("--delay")
        sp.add_argument("--jitter")
        sp.add_argument("--loss")
        sp.add_argument("--rate")
        sp.add_argument("--corrupt")
        sp.add_argument("--duplicate")
        sp.add_argument("--reorder")
        sp.add_argument("--queue")
        sp.add_argument("--distribution")

    sp = add("shape", cmd_shape, "change a running link (profile and/or individual parameters)")
    sp.add_argument("lab")
    sp.add_argument("link", help="link id, e.g. client--server")
    sp.add_argument("-p", "--profile", help="switch profile ('' to clear)")
    sp.add_argument("-d", "--direction", choices=["both", "up", "down"], default="both")
    sp.add_argument("--reset", action="store_true", help="drop previous live overrides")
    shaping_args(sp)

    sp = add("cut", lambda a: cmd_cut(a, True), "drop all traffic on a link")
    sp.add_argument("lab")
    sp.add_argument("link")
    sp = add("heal", lambda a: cmd_cut(a, False), "undo `cut`")
    sp.add_argument("lab")
    sp.add_argument("link")

    sp = add("exec", cmd_exec, "run a command (default: your shell) inside a lab node, as you")
    sp.add_argument("lab")
    sp.add_argument("node")
    sp.add_argument("--root", action="store_true", help="run as root instead of your user")
    sp.add_argument("cmd", nargs=argparse.REMAINDER)

    sp = add("pipe", cmd_pipe, "relay a local port through a shaped link: netsim pipe 6000:5000 -p 3g-hspa")
    sp.add_argument("spec", help="LISTEN:TARGET_PORT | LISTEN:HOST:PORT | ADDR:PORT:HOST:PORT")
    sp.add_argument("-p", "--profile", default="4g-lte")
    sp.add_argument("--udp", action="store_true")
    sp.add_argument("--name", help="lab name (default pipe-<port>)")
    shaping_args(sp)

    sp = add("run", cmd_run, "run a command through a shaped link: netsim run -p 3g-hspa -- curl URL")
    sp.add_argument("-p", "--profile", default="4g-lte")
    sp.add_argument("-f", "--forward", action="append", metavar="PORT[:HOSTPORT]",
                    help="make localhost:PORT inside reach this machine's localhost:HOSTPORT through the link")
    sp.add_argument("--forward-udp", action="append", metavar="PORT[:HOSTPORT]")
    sp.add_argument("--no-internet", action="store_true", help="only this machine is reachable, no NAT")
    sp.add_argument("--name", help="lab name (default run-<pid>)")
    sp.add_argument("-q", "--quiet", action="store_true")
    shaping_args(sp)
    sp.add_argument("cmd", nargs=argparse.REMAINDER)

    sp = add("logs", cmd_logs, "print a lab program's output")
    sp.add_argument("lab")
    sp.add_argument("program")
    sp.add_argument("-f", "--follow", action="store_true")

    sp = add("validate", cmd_validate, "check a lab or profile YAML file (offline)")
    sp.add_argument("file")
    sp.add_argument("--kind", choices=["lab", "profile"])

    add("cleanup", cmd_cleanup, "remove leftovers of crashed runs")
    return p


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    raw = sys.argv[1:] if argv is None else argv
    args = parser.parse_args(raw)
    if not getattr(args, "fn", None):
        parser.print_help()
        return
    if args.command in ("exec", "run") and args.cmd[:1] == ["--"]:
        args.cmd = args.cmd[1:]
    try:
        args.fn(args)
    except CliError as e:
        print(f"netsim: {e}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)
