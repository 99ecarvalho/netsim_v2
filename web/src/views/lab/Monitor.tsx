// SPDX-License-Identifier: LGPL-3.0-or-later
// Copyright (C) 2026 Eduardo Correia
import { Copy, Save, Scissors, Undo2, Zap } from "lucide-react";
import YAML from "yaml";
import { serialize, setIn } from "../../lib/labdoc";
import { useEffect, useMemo, useState } from "react";
import Chart, { type SeriesDef } from "../../components/Chart";
import Topology, { type Selection } from "../../components/Topology";
import ErrorBoundary from "../../components/ErrorBoundary";
import { Split, useTick } from "../../components/ui";
import { api, enc } from "../../lib/api";
import { fmtBps, fmtBytes, fmtTime } from "../../lib/format";
import { toastError, useStore } from "../../lib/store";
import type { Direction, LabEvent, LabSpec, Runtime, StatsSample } from "../../lib/types";

interface Props {
  name: string;
  data: LabSpec;
  runtime: Runtime;
  samples: StatsSample[];
  events: LabEvent[];
  editorDirty: boolean;
  onFileSaved: () => void;
}

const THROUGHPUT: SeriesDef[] = [
  { label: "a→b", color: "--up", fill: true },
  { label: "b→a", color: "--down", fill: true },
];
const DROPS: SeriesDef[] = [
  { label: "drops a→b", color: "--up", points: true },
  { label: "drops b→a", color: "--down", points: true },
];
const QUEUE: SeriesDef[] = [
  { label: "queued a→b", color: "--up" },
  { label: "queued b→a", color: "--down" },
];

const bps = (v: number) => fmtBps(v, 2);

export default function Monitor({ name, runtime, samples, events, editorDirty, onFileSaved }: Props) {
  const [selection, setSelection] = useState<Selection>(null);
  const latest = samples[samples.length - 1] ?? null;
  // the running lab's own spec, not the (maybe edited) file
  const data = runtime.lab;

  return (
    <Split
      id="monitor"
      initial={0.58}
      left={
        <div style={{ position: "relative", flex: 1, minHeight: 0 }}>
          <ErrorBoundary what="graph" resetKey={runtime.started_at}>
            <Topology data={data} plan={runtime.plan} runtime={runtime} stats={latest} editable={false} selection={selection} onSelect={setSelection} />
          </ErrorBoundary>
        </div>
      }
      right={
        <div className="scroll">
          {selection?.type === "link" ? (
            <LinkPanel key={selection.id} lab={name} id={selection.id} runtime={runtime} samples={samples}
              editorDirty={editorDirty} onFileSaved={onFileSaved} />
          ) : selection?.type === "node" ? (
            <NodePanel key={selection.id} lab={name} node={selection.id} runtime={runtime} />
          ) : (
            <Overview runtime={runtime} samples={samples} events={events} onSelect={setSelection} />
          )}
        </div>
      }
    />
  );
}

function series(samples: StatsSample[], id: string, field: "bps" | "dps" | "backlog") {
  const xs: number[] = [];
  const up: (number | null)[] = [];
  const down: (number | null)[] = [];
  for (const s of samples) {
    const l = s.links[id];
    xs.push(s.t);
    up.push(l?.up ? l.up[field] : null);
    down.push(l?.down ? l.down[field] : null);
  }
  return [xs, up, down];
}

// ----------------------------------------------------------------- overview

function Overview({ runtime, samples, events, onSelect }: {
  runtime: Runtime;
  samples: StatsSample[];
  events: LabEvent[];
  onSelect: (s: Selection) => void;
}) {
  const latest = samples[samples.length - 1];
  const total = useMemo(() => {
    const xs: number[] = [];
    const up: number[] = [];
    const down: number[] = [];
    for (const s of samples) {
      xs.push(s.t);
      let u = 0;
      let d = 0;
      for (const l of Object.values(s.links)) {
        u += l.up?.bps ?? 0;
        d += l.down?.bps ?? 0;
      }
      up.push(u);
      down.push(d);
    }
    return [xs, up, down];
  }, [samples]);

  return (
    <div className="col" style={{ padding: 12, gap: 12 }}>
      <div className="card">
        <div className="card-head">All links <span className="faint" style={{ fontWeight: 400 }}>· aggregate throughput</span></div>
        <div className="card-body">
          <Chart series={THROUGHPUT} data={total} yFormat={bps} window={120} />
        </div>
      </div>
      <div className="card">
        <div className="card-head">Links <span className="faint" style={{ fontWeight: 400 }}>· click a row or an edge for details</span></div>
        <table className="grid">
          <thead>
            <tr>
              <th>link</th>
              <th>profile</th>
              <th className="up">a→b</th>
              <th className="down">b→a</th>
              <th>drops/s</th>
            </tr>
          </thead>
          <tbody>
            {(runtime.lab.links ?? []).map((l, index) => {
              const id = l.name || `${l.a}--${l.b}`;
              const lr = runtime.links[id];
              const st = latest?.links[id];
              return (
                <tr key={id} style={{ cursor: "pointer" }} onClick={() => onSelect({ type: "link", id, index })}>
                  <td>{id}</td>
                  <td>
                    {lr?.profile ?? <span className="faint">none</span>}
                    {lr?.step_label && <span className="badge accent" style={{ marginLeft: 6 }}>{lr.step_label}</span>}
                    {Object.keys(lr?.runtime.up ?? {}).length + Object.keys(lr?.runtime.down ?? {}).length > 0 && (
                      <span className="badge warn" style={{ marginLeft: 6 }}>live override</span>
                    )}
                  </td>
                  <td className="mono">{bps(st?.up?.bps ?? 0)}</td>
                  <td className="mono">{bps(st?.down?.bps ?? 0)}</td>
                  <td className={`mono ${(st?.up?.dps ?? 0) + (st?.down?.dps ?? 0) > 0 ? "err" : "faint"}`}>
                    {((st?.up?.dps ?? 0) + (st?.down?.dps ?? 0)).toFixed(0)}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {Object.keys(runtime.proxies).length > 0 && (
        <div className="card">
          <div className="card-head">Proxies</div>
          <table className="grid">
            <thead>
              <tr>
                <th>proxy</th>
                <th>route</th>
                <th>conns</th>
                <th>bytes ↑/↓</th>
              </tr>
            </thead>
            <tbody>
              {Object.values(runtime.proxies).map((p) => {
                const st = latest?.proxies[p.name] ?? p.stats;
                return (
                  <tr key={p.name}>
                    <td>{p.name} <span className="faint">{p.proto}</span></td>
                    <td className="mono" style={{ fontSize: 11 }}>{p.error ? <span className="err">{p.error}</span> : p.route}</td>
                    <td className="mono">{st ? `${st.active}/${st.total}` : "-"}</td>
                    <td className="mono">{st ? `${fmtBytes(st.bytes_up)} / ${fmtBytes(st.bytes_down)}` : "-"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      <EventLog events={events} />
    </div>
  );
}

export function EventLog({ events }: { events: LabEvent[] }) {
  return (
    <div className="card">
      <div className="card-head">Events</div>
      <div className="log" style={{ maxHeight: 300, overflow: "auto" }}>
        {[...events].reverse().slice(0, 200).map((e, i) => (
          <div className="ln" key={i}>
            <span className="ts">{fmtTime(e.t)}</span>
            <span className={e.level}>{e.msg}</span>
          </div>
        ))}
        {!events.length && <span className="faint">nothing yet</span>}
      </div>
    </div>
  );
}

// ----------------------------------------------------------------- link panel

interface StepInfo {
  label: string | null;
  duration: string;
  up: { summary: string };
}

function LinkPanel({ lab, id, runtime, samples, editorDirty, onFileSaved }: {
  lab: string;
  id: string;
  runtime: Runtime;
  samples: StatsSample[];
  editorDirty: boolean;
  onFileSaved: () => void;
}) {
  const profiles = useStore((s) => s.profiles);
  const lr = runtime.links[id];
  const pl = runtime.plan.links[id];
  const latest = samples[samples.length - 1]?.links[id];
  const [dir, setDir] = useState<Direction | "both">("both");
  const [form, setForm] = useState<Record<string, string>>({});
  const [steps, setSteps] = useState<StepInfo[] | null>(null);
  useTick(1000);

  useEffect(() => {
    setSteps(null);
    if (!lr?.timeline || !lr.profile) return;
    api.get<{ text: string }>(`/api/profiles/${enc(lr.profile)}`)
      .then((p) => api.post<{ ok: boolean; steps: StepInfo[] }>("/api/profiles/validate", { text: p.text, name: lr.profile }))
      .then((v) => v.ok && setSteps(v.steps))
      .catch(() => {});
  }, [lr?.profile, lr?.timeline]);

  const tp = useMemo(() => series(samples, id, "bps"), [samples, id]);
  const dr = useMemo(() => series(samples, id, "dps"), [samples, id]);
  const q = useMemo(() => series(samples, id, "backlog"), [samples, id]);

  if (!lr || !pl) return <div className="empty">Unknown link</div>;
  const cut = lr.up?.summary === "blackhole" && lr.down?.summary === "blackhole";
  const patch = async (body: Record<string, unknown>) => {
    try {
      await api.patch(`/api/labs/${enc(lab)}/links/${enc(id)}`, body);
    } catch (e) {
      toastError(e);
    }
  };
  const overrides = { ...lr.runtime.up, ...lr.runtime.down };
  const liveOnly = (lr.profile ?? null) !== (lr.file_profile ?? null);

  /** Write the live profile into the lab file (comments kept); the daemon adopts it without restart. */
  const saveToFile = async () => {
    try {
      const { text } = await api.get<{ text: string }>(`/api/labs/${enc(lab)}`);
      const doc = YAML.parseDocument(text);
      const links = ((doc.toJS() ?? {}).links ?? []) as { a: string; b: string; name?: string }[];
      const idx = links.findIndex((l) => (l.name || `${l.a}--${l.b}`) === id);
      if (idx < 0) throw new Error(`link ${id} is not in the lab file any more`);
      setIn(doc, ["links", idx, "profile"], lr.profile ?? undefined);
      await api.put(`/api/labs/${enc(lab)}`, { text: serialize(doc) });
      onFileSaved();
      useStore.getState().toast("success", `${id}: profile ${lr.profile ?? "none"} saved to labs/${lab}.yaml`);
    } catch (e) {
      toastError(e);
    }
  };
  const hasOverrides = Object.keys(lr.runtime.up ?? {}).length + Object.keys(lr.runtime.down ?? {}).length > 0;

  return (
    <div className="col" style={{ padding: 12, gap: 12 }}>
      <div className="card">
        <div className="card-head">
          <span className="grow">{id}</span>
          <span className="faint" style={{ fontWeight: 400 }}>{pl.a.node} ({pl.a.ip ?? pl.a.name}) ⇄ {pl.b.node} ({pl.b.ip ?? pl.b.name})</span>
        </div>
        <div className="card-body col">
          <div className="row">
            <select className="grow" value={lr.profile ?? ""} onChange={(e) => patch({ set_profile: true, profile: e.target.value || null })}>
              <option value="">— no profile —</option>
              {profiles.map((p) => (
                <option key={p.name} value={p.name}>{p.category ? `${p.category} / ` : ""}{p.name}{p.timeline ? " ⏱" : ""}</option>
              ))}
            </select>
            {cut ? (
              <button className="btn ok" onClick={() => patch({ params: { both: { blackhole: null } } })}>
                <Undo2 size={14} /> Heal
              </button>
            ) : (
              <button className="btn danger" onClick={() => patch({ params: { both: { blackhole: true } } })} title="Drop all packets in both directions">
                <Scissors size={14} /> Cut
              </button>
            )}
          </div>
          {liveOnly && (
            <div className="row badge warn" style={{ whiteSpace: "normal", padding: "6px 10px", borderRadius: 8 }}>
              <span className="grow">
                Live change: the file says <b>{lr.file_profile ?? "no profile"}</b>. It goes back to that on restart.
              </span>
              <button className="btn small" onClick={saveToFile} disabled={editorDirty}
                title={editorDirty ? "Save or discard your edits in Design first" : "Write this profile into the lab file"}>
                <Save size={12} /> Save to lab
              </button>
            </div>
          )}
          {cut && (
            <span className="faint" style={{ fontSize: 11 }}>Cut is a live override: Heal, or pick a profile, to bring the link back.</span>
          )}
          <div className="kv mono" style={{ fontSize: 11.5 }}>
            <dt className="up">a→b</dt>
            <dd>{lr.up?.summary}</dd>
            <dt className="down">b→a</dt>
            <dd>{lr.down?.summary}</dd>
          </div>
          {steps && (
            <div className="col" style={{ gap: 4 }}>
              <div className="timeline-bar">
                {steps.map((s, i) => (
                  <div key={i} className={`${s.up.summary === "blackhole" ? "cut" : ""} ${lr.step === i ? "on" : ""}`}
                    style={{ flex: dur(s.duration), background: lr.step === i ? "var(--accent-soft)" : undefined }}
                    title={`${s.label ?? `step ${i + 1}`} · ${s.duration}\n${s.up.summary}`}>
                    {s.label ?? i + 1}
                  </div>
                ))}
              </div>
              <span className="faint" style={{ fontSize: 11 }}>
                timeline: <b>{lr.step_label}</b>{lr.step_remaining != null && ` · next in ${Math.max(0, lr.step_remaining - 0).toFixed(0)}s`}
              </span>
            </div>
          )}
        </div>
      </div>

      <div className="card">
        <div className="card-head"><span className="grow">Throughput</span></div>
        <div className="card-body">
          <div className="stats" style={{ marginBottom: 8 }}>
            <Stat v={bps(latest?.up?.bps ?? 0)} l="a→b bit/s" cls="up" />
            <Stat v={bps(latest?.down?.bps ?? 0)} l="b→a bit/s" cls="down" />
            <Stat v={fmtBytes(latest?.up?.bytes)} l="a→b total" />
            <Stat v={fmtBytes(latest?.down?.bytes)} l="b→a total" />
            <Stat v={String((latest?.up?.drops ?? 0) + (latest?.down?.drops ?? 0))} l="dropped pkts" cls={(latest?.up?.drops ?? 0) + (latest?.down?.drops ?? 0) ? "err" : ""} />
          </div>
          <Chart series={THROUGHPUT} data={tp} yFormat={bps} window={120} />
        </div>
      </div>
      <div className="card">
        <div className="card-head">Drops / s and queue</div>
        <div className="card-body col">
          <Chart series={DROPS} data={dr} height={110} yFormat={(v) => v.toFixed(0)} window={120} />
          <Chart series={QUEUE} data={q} height={110} yFormat={(v) => fmtBytes(v)} window={120} />
          <span className="faint" style={{ fontSize: 11 }}>"queued" is what netem holds: packets being delayed plus packets waiting for bandwidth.</span>
        </div>
      </div>

      <div className="card">
        <div className="card-head">
          <Zap size={14} /> <span className="grow">Live override</span>
          <div className="seg">
            {(["both", "up", "down"] as const).map((d) => (
              <button key={d} className={dir === d ? "on" : ""} onClick={() => setDir(d)}>{d === "both" ? "both" : d === "up" ? "a→b" : "b→a"}</button>
            ))}
          </div>
        </div>
        <div className="card-body col">
          <div className="fields">
            {[
              ["delay", "e.g. 120ms"],
              ["jitter", "e.g. 30ms"],
              ["loss", "e.g. 2%"],
              ["rate", "e.g. 1mbit"],
              ["duplicate", "e.g. 1%"],
              ["reorder", "e.g. 5%"],
            ].map(([k, ph]) => (
              <label className="field" key={k}>
                <span>{k}{overrides[k] !== undefined && <span className="badge warn" style={{ marginLeft: 4 }}>{String(overrides[k])}</span>}</span>
                <input className="mono" placeholder={ph} value={form[k] ?? ""} onChange={(e) => setForm({ ...form, [k]: e.target.value })} />
              </label>
            ))}
          </div>
          <div className="row">
            <button className="btn primary" onClick={() => {
              const params = Object.fromEntries(Object.entries(form).filter(([, v]) => v.trim()).map(([k, v]) => [k, v.trim()]));
              if (Object.keys(params).length) patch({ params: { [dir]: params } }).then(() => setForm({}));
            }}>Apply</button>
            <button className="btn" disabled={!hasOverrides} onClick={() => patch({ reset: true })}>Reset overrides</button>
            <span className="faint" style={{ fontSize: 11 }}>Applied immediately with tc. Temporary: switching profile or restarting drops them.</span>
          </div>
        </div>
      </div>

      <details className="card">
        <summary className="card-head" style={{ cursor: "pointer" }}>netem commands</summary>
        <div className="card-body">
          <pre className="snippet mono" style={{ whiteSpace: "pre-wrap" }}>
            {`# ${pl.a.node} egress (a→b)\ntc qdisc replace dev ${pl.a.name} root handle 1: netem ${lr.up?.args.join(" ")}\n\n# ${pl.b.node} egress (b→a)\ntc qdisc replace dev ${pl.b.name} root handle 1: netem ${lr.down?.args.join(" ")}`}
          </pre>
        </div>
      </details>
    </div>
  );
}

function dur(s: string) {
  const m = /^([\d.]+)\s*(ms|s|min)?$/.exec(String(s).trim());
  if (!m) return 1;
  const v = Number(m[1]);
  return m[2] === "ms" ? v / 1000 : m[2] === "min" ? v * 60 : v;
}

function Stat({ v, l, cls = "" }: { v: string; l: string; cls?: string }) {
  return (
    <div className="stat">
      <span className={`v ${cls}`}>{v}</span>
      <span className="l">{l}</span>
    </div>
  );
}

// ----------------------------------------------------------------- node panel

interface NodeInfo {
  node: { name: string; kind: string; ns: string | null; ip: string | null };
  addr: { ifname: string; addr_info?: { local: string; prefixlen: number; family: string }[]; operstate: string; mtu: number }[];
  routes: { dst: string; gateway?: string; dev: string }[];
  tcp?: { state: string; local: string; peer: string; rtt?: number; rttvar?: number; cwnd?: number; retrans_total?: number;
    bytes_sent?: number; bytes_received?: number; process?: string; send?: string; delivery_rate?: string }[];
  listening?: { proto: string; local: string; process?: string }[];
}

function NodePanel({ lab, node, runtime }: { lab: string; node: string; runtime: Runtime }) {
  const [info, setInfo] = useState<NodeInfo | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    const load = () =>
      api.get<NodeInfo>(`/api/labs/${enc(lab)}/nodes/${enc(node)}`)
        .then((i) => alive && (setInfo(i), setErr(null)))
        .catch((e) => alive && setErr(String(e.message ?? e)));
    load();
    const h = setInterval(load, 2000);
    return () => {
      alive = false;
      clearInterval(h);
    };
  }, [lab, node]);

  const pn = runtime.plan.nodes[node];
  const programs = Object.values(runtime.programs).filter((p) => p.node === node);
  const cmd = `netsim exec ${lab} ${node}`;

  return (
    <div className="col" style={{ padding: 12, gap: 12 }}>
      <div className="card">
        <div className="card-head">
          <span className="grow">{node}</span>
          <span className="badge">{pn?.kind}</span>
        </div>
        <div className="card-body col">
          <dl className="kv">
            <dt>namespace</dt>
            <dd className="mono">{pn?.ns ?? "host (root namespace)"}</dd>
            <dt>hostname</dt>
            <dd className="mono">{node} → {pn?.ip ?? "-"}</dd>
          </dl>
          {pn?.kind !== "switch" && (
            <div className="row">
              <code className="snippet grow" style={{ margin: 0, padding: "4px 8px" }}>{cmd} -- your-program</code>
              <button className="icon-btn" title="Copy" onClick={() => navigator.clipboard.writeText(`${cmd} -- `)}>
                <Copy size={14} />
              </button>
            </div>
          )}
          {programs.map((p) => (
            <div key={p.name} className="row">
              <span className={`dot ${p.status === "running" ? "running" : p.status === "failed" ? "failed" : ""}`} />
              <b>{p.name}</b> <span className="mono faint grow" style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{p.cmd}</span>
              <span className="faint">{p.status}</span>
            </div>
          ))}
        </div>
      </div>
      {err && <div className="banner error">{err}</div>}
      {info?.tcp && (
        <div className="card">
          <div className="card-head">TCP connections <span className="faint" style={{ fontWeight: 400 }}>· kernel view (ss), refreshes every 2s</span></div>
          {info.tcp.length ? (
            <div style={{ overflow: "auto" }}>
              <table className="grid compact">
                <thead>
                  <tr>
                    <th>local → peer</th>
                    <th>rtt</th>
                    <th>cwnd</th>
                    <th>retrans</th>
                    <th>sent/recv</th>
                    <th>proc</th>
                  </tr>
                </thead>
                <tbody>
                  {info.tcp.map((c, i) => (
                    <tr key={i}>
                      <td className="mono">{c.local} → {c.peer}</td>
                      <td className="mono">{c.rtt != null ? `${c.rtt.toFixed(1)}ms ±${(c.rttvar ?? 0).toFixed(0)}` : "-"}</td>
                      <td className="mono">{c.cwnd ?? "-"}</td>
                      <td className={`mono ${c.retrans_total ? "err" : ""}`}>{c.retrans_total ?? 0}</td>
                      <td className="mono">{fmtBytes(c.bytes_sent)} / {fmtBytes(c.bytes_received)}</td>
                      <td>{c.process ?? ""}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="card-body faint">no established connections</div>
          )}
        </div>
      )}
      {info?.listening && info.listening.length > 0 && (
        <div className="card">
          <div className="card-head">Listening</div>
          <table className="grid compact">
            <tbody>
              {info.listening.map((l, i) => (
                <tr key={i}>
                  <td>{l.proto}</td>
                  <td className="mono">{l.local}</td>
                  <td>{l.process ?? ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {info && (
        <div className="card">
          <div className="card-head">Interfaces & routes</div>
          <div className="card-body col">
            <table className="grid compact">
              <tbody>
                {info.addr.filter((a) => a.ifname !== "lo").map((a) => (
                  <tr key={a.ifname}>
                    <td className="mono">{a.ifname}</td>
                    <td className="mono">{(a.addr_info ?? []).filter((x) => x.family === "inet").map((x) => `${x.local}/${x.prefixlen}`).join(", ")}</td>
                    <td className="faint">{a.operstate} · mtu {a.mtu}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <table className="grid compact">
              <tbody>
                {info.routes.map((r, i) => (
                  <tr key={i}>
                    <td className="mono">{r.dst}</td>
                    <td className="mono faint">{r.gateway ? `via ${r.gateway}` : "direct"} dev {r.dev}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
