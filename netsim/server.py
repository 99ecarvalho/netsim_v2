# SPDX-License-Identifier: LGPL-3.0-or-later
# Copyright (C) 2026 Eduardo Correia
"""HTTP + WebSocket API and the web UI.

The daemon runs as root and can start arbitrary programs, so every request must
carry the token stored in RUN_DIR/token (readable only by the invoking user).
Browsers get it once via ?token=..., which is turned into a cookie.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware

from netsim import __version__
from netsim.bus import Bus
from netsim.config import RUN_DIR, chown_to_user, home_dir, seed_home, web_dir
from netsim.engine import DIRECTIONS, Engine, EngineError
from netsim.models import Lab, Link, Profile
from netsim.shaping import LinkShape, ShapingError, flatten_profile, netem
from netsim.store import LabStore, ProfileStore, StoreError, parse_yaml
from netsim.units import UnitError

log = logging.getLogger("netsim.server")
COOKIE = "netsim_token"
LOCAL_HOSTS = {"localhost", "127.0.0.1", "[::1]", "::1"}


def write_token(run_dir: Path) -> str:
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / "token"
    token = secrets.token_urlsafe(24)
    path.write_text(token)
    path.chmod(0o600)
    chown_to_user(path)
    return token


class TextBody(BaseModel):
    text: str


class ValidateBody(BaseModel):
    text: str
    name: str | None = None


class RenameBody(BaseModel):
    to: str


class LinkPatch(BaseModel):
    profile: str | None = None
    set_profile: bool = False
    params: dict[str, dict[str, Any]] | None = None
    reset: bool = False


class CaptureBody(BaseModel):
    link: str
    side: str = "a"
    filter: str = ""


class ProbeBody(BaseModel):
    kind: str = "ping"
    src: str
    dst: str
    options: dict[str, Any] = {}


class EphemeralBody(BaseModel):
    name: str
    text: str


def create_app(engine: Engine, token: str, check_host: bool = True) -> FastAPI:

    @asynccontextmanager
    async def lifespan(app):
        await engine.start()
        try:
            yield
        finally:
            await engine.stop()

    app = FastAPI(title="netsim", version=__version__, lifespan=lifespan, docs_url="/api/docs",
                  openapi_url="/api/openapi.json")

    def authorized(headers, cookies, query) -> bool:
        cand = cookies.get(COOKIE) or query.get("token") or ""
        auth = headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            cand = auth[7:]
        return hmac.compare_digest(cand.encode(), token.encode())

    def host_ok(headers) -> bool:
        if not check_host:
            return True
        host = headers.get("host", "")
        host = host.split("]")[0] + "]" if host.startswith("[") else host.rsplit(":", 1)[0]
        return host in LOCAL_HOSTS

    class Auth(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            if not host_ok(request.headers):  # DNS-rebinding protection
                return JSONResponse({"detail": "forbidden host"}, 403)
            q = request.query_params
            if "token" in q and hmac.compare_digest(q["token"].encode(), token.encode()) \
                    and not request.url.path.startswith("/api/"):
                resp = RedirectResponse(request.url.path)
                resp.set_cookie(COOKIE, token, httponly=True, samesite="strict")
                return resp
            if request.url.path.startswith("/api/") and not authorized(request.headers, request.cookies, q):
                return JSONResponse({"detail": "missing or invalid token (see `netsim ui`)"}, 401)
            return await call_next(request)

    app.add_middleware(Auth)

    @app.exception_handler(EngineError)
    async def _engine_err(_, e: EngineError):
        return JSONResponse({"detail": str(e)}, 409)

    @app.exception_handler(StoreError)
    async def _store_err(_, e: StoreError):
        return JSONResponse({"detail": str(e), "details": e.details}, 422)

    @app.exception_handler(KeyError)
    async def _key_err(_, e: KeyError):
        return JSONResponse({"detail": f"not found: {e.args[0] if e.args else ''}"}, 404)

    @app.exception_handler(ValueError)
    async def _value_err(_, e: ValueError):
        return JSONResponse({"detail": str(e)}, 400)

    profiles, labs = engine.profiles, engine.labs

    # ------------------------------------------------------------ info

    @app.get("/api/info")
    async def info():
        return {"version": __version__, "user": engine.user.name, "home": str(home_dir()),
                "profiles_dir": str(profiles.dir), "labs_dir": str(labs.dir), "run_dir": str(engine.run_dir)}

    @app.get("/api/schema")
    async def schema():
        return {"profile": Profile.model_json_schema(), "lab": Lab.model_json_schema()}

    @app.post("/api/cleanup")
    async def cleanup():
        return {"removed": await engine.cleanup_stale()}

    # ------------------------------------------------------------ profiles

    def profile_summary(name: str) -> dict:
        e = profiles.entry(name)
        out: dict[str, Any] = {"name": name, "error": str(e.error) if e.error else None}
        if e.obj:
            out.update(title=e.obj.title, description=e.obj.description, category=e.obj.category,
                       tags=e.obj.tags, extends=e.obj.extends)
            try:
                flat = flatten_profile(name, profiles.get)
                out["timeline"] = len(flat.timeline.steps) if flat.timeline else 0
                out["category"] = flat.category
                out["summary"] = {d: netem(_eff(flat, d)).summary for d in DIRECTIONS}
            except (KeyError, StoreError, ShapingError, UnitError) as err:
                out["error"] = str(err)
        return out

    @app.get("/api/profiles")
    async def list_profiles():
        return [profile_summary(n) for n in profiles.names()]

    @app.get("/api/profiles/{name}")
    async def get_profile(name: str):
        e = profiles.entry(name)
        return {"name": name, "text": e.text, "error": str(e.error) if e.error else None,
                "details": e.error.details if e.error else [], "summary": profile_summary(name)}

    @app.post("/api/profiles/validate")
    async def validate_profile(body: ValidateBody):
        try:
            prof = parse_yaml(body.text, Profile, body.name or "_preview")
            lookup = lambda n: prof if n == (body.name or "_preview") else profiles.get(n)  # noqa: E731
            flat = flatten_profile(body.name or "_preview", lookup)
            steps = flat.timeline.steps if flat.timeline else []
            preview = {d: _render(_eff(flat, d)) for d in DIRECTIONS}
            step_preview = [{"label": s.label, "duration": s.duration,
                             **{d: _render(_eff(flat, d, s)) for d in DIRECTIONS}} for s in steps]
            return {"ok": True, "preview": preview, "steps": step_preview}
        except StoreError as e:
            return {"ok": False, "error": str(e), "details": e.details}
        except (ShapingError, UnitError, KeyError, ValueError) as e:
            return {"ok": False, "error": f"unknown profile {e}" if isinstance(e, KeyError) else str(e),
                    "details": []}

    @app.put("/api/profiles/{name}")
    async def put_profile(name: str, body: TextBody):
        profiles.save(name, body.text)
        engine.bus.publish("profiles", {"changed": name})
        return {**profile_summary(name), "reapplied": await engine.profile_saved(name)}

    @app.post("/api/profiles/{name}/rename")
    async def rename_profile(name: str, body: RenameBody):
        profiles.rename(name, body.to)
        engine.bus.publish("profiles", {"renamed": name, "to": body.to})
        return {"ok": True}

    @app.delete("/api/profiles/{name}")
    async def delete_profile(name: str):
        profiles.delete(name)
        engine.bus.publish("profiles", {"deleted": name})
        return {"ok": True}

    # ------------------------------------------------------------ labs

    @app.get("/api/labs")
    async def list_labs():
        return engine.summary()

    @app.get("/api/labs/{name}")
    async def get_lab(name: str):
        e = labs.entry(name)
        return {"name": name, "text": e.text, "error": str(e.error) if e.error else None,
                "details": e.error.details if e.error else [], "running": name in engine.running,
                "ephemeral": labs.is_ephemeral(name)}

    @app.post("/api/labs/validate")
    async def validate_lab(body: ValidateBody):
        name = body.name or "_preview"
        try:
            lab = parse_yaml(body.text, Lab, name)
            return {"ok": True, "plan": await engine.preview(lab, name)}
        except StoreError as e:
            return {"ok": False, "error": str(e), "details": e.details}
        except EngineError as e:
            return {"ok": False, "error": str(e), "details": [{"loc": e.loc, "msg": str(e)}] if e.loc else []}
        except ValueError as e:
            return {"ok": False, "error": str(e), "details": []}

    @app.put("/api/labs/{name}")
    async def put_lab(name: str, body: TextBody):
        labs.save(name, body.text, ephemeral=labs.is_ephemeral(name))
        result = await engine.adopt(name, body.text) if name in engine.running else {"live": [], "restart": False}
        engine._labs_changed()
        return {"ok": True, **result}

    @app.post("/api/labs/{name}/rename")
    async def rename_lab(name: str, body: RenameBody):
        if name in engine.running:
            raise EngineError("stop the lab before renaming it")
        labs.rename(name, body.to)
        engine._labs_changed()
        return {"ok": True}

    @app.delete("/api/labs/{name}")
    async def delete_lab(name: str):
        if name in engine.running:
            raise EngineError("stop the lab before deleting it")
        labs.delete(name)
        engine._labs_changed()
        return {"ok": True}

    @app.post("/api/labs/ephemeral")
    async def ephemeral_lab(body: EphemeralBody):
        if labs.exists(body.name) and not labs.is_ephemeral(body.name):
            raise EngineError(f"a saved lab named {body.name} exists")
        labs.save(body.name, body.text, ephemeral=True)
        try:
            await engine.up(body.name)
        except EngineError:
            labs.delete(body.name)
            raise
        return engine.runtime(body.name)

    @app.post("/api/labs/{name}/up")
    async def lab_up(name: str):
        await engine.up(name)
        return engine.runtime(name)

    @app.post("/api/labs/{name}/down")
    async def lab_down(name: str):
        await engine.down(name)
        return {"ok": True}

    @app.post("/api/labs/{name}/restart")
    async def lab_restart(name: str):
        await engine.restart(name)
        return engine.runtime(name)

    @app.get("/api/labs/{name}/runtime")
    async def lab_runtime(name: str):
        return engine.runtime(name)

    @app.get("/api/labs/{name}/history")
    async def lab_history(name: str, since: float = 0):
        return engine.history(name, since)

    @app.get("/api/labs/{name}/events")
    async def lab_events(name: str):
        rt = engine.running.get(name)
        return list(rt.events) if rt else []

    @app.get("/api/labs/{name}/commands")
    async def lab_commands(name: str):
        rt = engine.running.get(name)
        return [c.to_json() for c in rt.runner.history] if rt else []

    @app.patch("/api/labs/{name}/links/{link_id}")
    async def patch_link(name: str, link_id: str, body: LinkPatch):
        return await engine.set_link(name, link_id, profile=body.profile if body.set_profile else False,
                                     params=body.params, reset=body.reset)

    @app.post("/api/labs/{name}/programs/{prog}/{action}")
    async def program_action(name: str, prog: str, action: str):
        if action not in ("start", "stop", "restart", "signal"):
            raise HTTPException(400, "bad action")
        return await engine.program_action(name, prog, action)

    @app.get("/api/labs/{name}/programs/{prog}/logs")
    async def program_logs(name: str, prog: str):
        rt = engine._rt(name)
        return list(rt.programs[prog].lines)

    @app.post("/api/labs/{name}/proxies/{proxy}/{action}")
    async def proxy_action(name: str, proxy: str, action: str):
        if action not in ("start", "stop", "restart"):
            raise HTTPException(400, "bad action")
        return await engine.proxy_action(name, proxy, action)

    @app.get("/api/labs/{name}/nodes/{node}")
    async def node_info(name: str, node: str):
        return await engine.node_info(name, node)

    @app.post("/api/labs/{name}/captures")
    async def capture_start(name: str, body: CaptureBody):
        return (await engine.capture_start(name, body.link, body.side, body.filter)).to_json()

    @app.post("/api/captures/{cid}/stop")
    async def capture_stop(cid: str):
        return (await engine.capture_stop(cid)).to_json()

    @app.delete("/api/captures/{cid}")
    async def capture_delete(cid: str):
        await engine.capture_stop(cid)
        engine.capture_delete(cid)
        return {"ok": True}

    @app.get("/api/captures/{cid}/pcap")
    async def capture_pcap(cid: str):
        cap = engine.captures.get(cid)
        if not cap or not cap.path.exists():
            raise HTTPException(404, "capture not found")
        return FileResponse(cap.path, media_type="application/vnd.tcpdump.pcap", filename=cap.path.name)

    @app.post("/api/labs/{name}/probes")
    async def probe_start(name: str, body: ProbeBody):
        return (await engine.probe_start(name, body.kind, body.src, body.dst, body.options)).to_json()

    @app.post("/api/probes/{pid}/stop")
    async def probe_stop(pid: str):
        return (await engine.probe_stop(pid)).to_json()

    @app.get("/api/probes/{pid}")
    async def probe_get(pid: str):
        p = engine.probes.get(pid)
        if not p:
            raise HTTPException(404, "probe not found")
        return p.to_json(with_results=True)

    # ------------------------------------------------------------ websockets

    async def ws_auth(ws: WebSocket) -> bool:
        if not host_ok(ws.headers) or not authorized(ws.headers, ws.cookies, ws.query_params):
            await ws.close(code=4401)
            return False
        return True

    @app.websocket("/api/ws")
    async def ws_events(ws: WebSocket):
        if not await ws_auth(ws):
            return
        await ws.accept()
        sub = engine.bus.subscribe()

        async def sender():
            while True:
                topic, data = await sub.queue.get()
                await ws.send_text(json.dumps({"topic": topic, "data": data}, default=str))

        task = asyncio.create_task(sender())
        try:
            while True:
                msg = json.loads(await ws.receive_text())
                topics = set(msg.get("topics", []))
                if msg.get("op") == "sub":
                    sub.patterns |= topics
                elif msg.get("op") == "unsub":
                    sub.patterns -= topics
                elif msg.get("op") == "ping":
                    await ws.send_text(json.dumps({"topic": "pong", "data": None}))
        except (WebSocketDisconnect, RuntimeError, ValueError):
            pass
        finally:
            task.cancel()
            sub.close()

    @app.websocket("/api/term")
    async def ws_term(ws: WebSocket):
        if not await ws_auth(ws):
            return
        await ws.accept()
        q = ws.query_params
        out_q: asyncio.Queue[bytes | None] = asyncio.Queue()
        try:
            proc = await engine.open_terminal(q["lab"], q["node"], q.get("root") == "1",
                                              int(q.get("cols", 100)), int(q.get("rows", 30)),
                                              out_q.put_nowait, lambda code: out_q.put_nowait(None))
        except (EngineError, KeyError, OSError) as e:
            await ws.send_text(json.dumps({"type": "error", "message": str(e)}))
            await ws.close()
            return

        async def sender():
            while (data := await out_q.get()) is not None:
                await ws.send_bytes(data)
            await ws.send_text(json.dumps({"type": "exit"}))
            await ws.close()

        task = asyncio.create_task(sender())
        try:
            while True:
                msg = await ws.receive()
                if msg["type"] == "websocket.disconnect":
                    break
                if msg.get("bytes"):
                    proc.write(msg["bytes"])
                elif msg.get("text"):
                    m = json.loads(msg["text"])
                    if m.get("type") == "input":
                        proc.write(m["data"].encode())
                    elif m.get("type") == "resize":
                        proc.resize(int(m["cols"]), int(m["rows"]))
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            task.cancel()
            await proc.stop(1)

    # ------------------------------------------------------------ static UI

    static = web_dir()

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str):
        if path.startswith("api/"):
            raise HTTPException(404)
        if static is None:
            return Response("netsim web UI is not built. Run `make web` (needs node/npm).\n",
                            media_type="text/plain", status_code=503)
        candidate = (static / path).resolve()
        if path and candidate.is_file() and static in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(static / "index.html", headers={"Cache-Control": "no-cache"})

    return app


def _eff(profile: Profile, direction: str, step=None) -> dict:
    return LinkShape(profile, Link(a="a", b="b")).effective(direction, step)  # type: ignore[arg-type]


def _render(params: dict) -> dict:
    spec = netem(params)
    return {"summary": spec.summary, "tc": "tc qdisc replace dev <if> root handle 1: netem " + " ".join(spec.args)}


def build(run_dir: Path = RUN_DIR, home: Path | None = None, user_path: str | None = None,
          check_host: bool = True) -> tuple[FastAPI, str]:
    """Create the app. The caller must hold acquire_instance_lock(run_dir)."""
    home = home or home_dir()
    if copied := seed_home(home):
        log.info("copied %d example profiles/labs to %s", len(copied), home)
    bus = Bus()
    engine = Engine(ProfileStore(home / "profiles"), LabStore(home / "labs"), bus, run_dir, user_path=user_path)
    token = write_token(run_dir)
    return create_app(engine, token, check_host), token
