import { OctagonX, Play, RotateCw, Square } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Empty, Split, useTick } from "../../components/ui";
import { api, enc } from "../../lib/api";
import { fmtAgo, fmtBytes, fmtTime } from "../../lib/format";
import { toastError } from "../../lib/store";
import type { ProgramRuntime, Runtime } from "../../lib/types";
import { useTopic } from "../../lib/ws";

export default function Programs({ name, runtime }: { name: string; runtime: Runtime }) {
  const programs = Object.values(runtime.programs);
  const [selected, setSelected] = useState<string | null>(programs[0]?.name ?? null);
  useTick(1000);

  const act = (p: string, action: string) =>
    api.post(`/api/labs/${enc(name)}/programs/${enc(p)}/${action}`).catch(toastError);

  const left = (
    <div className="scroll">
      <table className="grid">
        <thead>
          <tr>
            <th>program</th>
            <th>node</th>
            <th>status</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {programs.map((p) => (
            <tr key={p.name} className={p.name === selected ? "sel" : ""} style={{ cursor: "pointer" }} onClick={() => setSelected(p.name)}>
              <td>
                <b>{p.name}</b>
                <div className="mono faint" style={{ fontSize: 11, maxWidth: 260, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={p.cmd}>{p.cmd}</div>
              </td>
              <td>{p.node}</td>
              <td><Status p={p} /></td>
              <td onClick={(e) => e.stopPropagation()} style={{ whiteSpace: "nowrap" }}>
                {p.status === "running" ? (
                  <>
                    <button className="icon-btn sm" title="Restart" onClick={() => act(p.name, "restart")}><RotateCw size={13} /></button>
                    <button className="icon-btn sm" title="Send Ctrl-C" onClick={() => act(p.name, "signal")}><OctagonX size={13} /></button>
                    <button className="icon-btn sm" title="Stop" onClick={() => act(p.name, "stop")}><Square size={13} /></button>
                  </>
                ) : (
                  <button className="icon-btn sm" title="Start" onClick={() => act(p.name, "start")}><Play size={13} /></button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {!programs.length && (
        <Empty title="No programs in this lab">
          Add a <code>programs:</code> entry (or use the node inspector in Design). Programs run inside their node as your user, with
          output captured here.
        </Empty>
      )}
      {Object.keys(runtime.proxies).length > 0 && (
        <>
          <div className="pane-head" style={{ marginTop: 12 }}><b>Proxies</b></div>
          <table className="grid">
            <tbody>
              {Object.values(runtime.proxies).map((px) => (
                <tr key={px.name}>
                  <td>
                    <b>{px.name}</b> <span className="faint">{px.proto}</span>
                    <div className="mono faint" style={{ fontSize: 11 }}>{px.route}</div>
                    {px.error && <div className="err" style={{ fontSize: 11 }}>{px.error}</div>}
                  </td>
                  <td className="mono" style={{ fontSize: 11 }}>
                    {px.stats && `${px.stats.active}/${px.stats.total} conns · ${fmtBytes(px.stats.bytes_up)}↑ ${fmtBytes(px.stats.bytes_down)}↓`}
                  </td>
                  <td>
                    {px.running ? (
                      <button className="icon-btn sm" title="Stop" onClick={() => api.post(`/api/labs/${enc(name)}/proxies/${enc(px.name)}/stop`).catch(toastError)}><Square size={13} /></button>
                    ) : (
                      <button className="icon-btn sm" title="Start" onClick={() => api.post(`/api/labs/${enc(name)}/proxies/${enc(px.name)}/start`).catch(toastError)}><Play size={13} /></button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </div>
  );

  return (
    <Split
      id="programs"
      initial={0.38}
      left={left}
      right={selected && runtime.programs[selected] ? <LogView key={selected} lab={name} program={runtime.programs[selected]} /> : <Empty title="Select a program" />}
    />
  );
}

function Status({ p }: { p: ProgramRuntime }) {
  const cls = p.status === "running" ? "ok" : p.status === "failed" ? "danger" : p.status === "starting" ? "warn" : "";
  return (
    <span className={`badge ${cls}`}>
      {p.status}
      {p.status === "running" && p.started_at && ` · ${fmtAgo(p.started_at)}`}
      {p.exit_code != null && p.status !== "running" && ` (${p.exit_code})`}
      {p.restarts > 0 && ` · ${p.restarts}×`}
    </span>
  );
}

function LogView({ lab, program }: { lab: string; program: ProgramRuntime }) {
  const [lines, setLines] = useState<{ t: number; line: string }[]>([]);
  const box = useRef<HTMLDivElement>(null);
  const stick = useRef(true);
  const [wrap, setWrap] = useState(true);

  useEffect(() => {
    api.get<{ t: number; line: string }[]>(`/api/labs/${enc(lab)}/programs/${enc(program.name)}/logs`).then(setLines).catch(() => {});
  }, [lab, program.name]);
  useTopic(`program:${lab}:${program.name}`, (d: { t: number; line: string }) => setLines((xs) => [...xs.slice(-4999), d]));
  useEffect(() => {
    if (stick.current && box.current) box.current.scrollTop = box.current.scrollHeight;
  }, [lines]);

  return (
    <>
      <div className="pane-head">
        <b>{program.name}</b> <span className="mono">{program.cmd}</span>
        <span className="grow" />
        <label className="row" style={{ gap: 4 }}><input type="checkbox" checked={wrap} onChange={(e) => setWrap(e.target.checked)} /> wrap</label>
        <button className="btn small" onClick={() => setLines([])}>Clear</button>
      </div>
      <div className="scroll log" ref={box} style={{ whiteSpace: wrap ? "pre-wrap" : "pre" }} onScroll={(e) => {
        const el = e.currentTarget;
        stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
      }}>
        {lines.map((l, i) => (
          <div className="ln" key={i}>
            <span className="ts">{fmtTime(l.t)}</span>
            <span className={l.line.startsWith("[netsim]") ? "netsim" : ""}>{l.line}</span>
          </div>
        ))}
        {!lines.length && <span className="faint">no output yet</span>}
      </div>
    </>
  );
}
