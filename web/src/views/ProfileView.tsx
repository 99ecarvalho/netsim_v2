// SPDX-License-Identifier: LGPL-3.0-or-later
// Copyright (C) 2026 Eduardo Correia
import { Copy, Files, Save, Trash2, Undo2 } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import YAML from "yaml";
import CodeEditor from "../components/CodeEditor";
import { createProfile } from "../components/Sidebar";
import { prompt, Split } from "../components/ui";
import { api, enc } from "../lib/api";
import { debounce } from "../lib/format";
import { lineOf } from "../lib/labdoc";
import { toastError, useStore } from "../lib/store";
import type { ValidationDetail } from "../lib/types";
import { Lazy } from "./lab/Inspector";

interface Rendered {
  summary: string;
  tc: string;
}
interface ProfileValidation {
  ok: boolean;
  error?: string;
  details?: ValidationDetail[];
  preview?: { up: Rendered; down: Rendered };
  steps?: { label: string | null; duration: string; up: Rendered; down: Rendered }[];
}

const DISTS = ["uniform", "normal", "pareto", "paretonormal", "exponential", "lognormal", "weibull"];

export default function ProfileView({ name, tabId }: { name: string; tabId: string }) {
  const setDirty = useStore((s) => s.setDirty);
  const profiles = useStore((s) => s.profiles);
  const [saved, setSaved] = useState<string | null>(null);
  const [text, setText] = useState<string | null>(null);
  const [v, setV] = useState<ProfileValidation | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    api.get<{ text: string }>(`/api/profiles/${enc(name)}`)
      .then((p) => {
        setSaved(p.text);
        setText(p.text);
      })
      .catch((e) => setErr(e.message));
  }, [name]);

  const dirty = text !== null && text !== saved;
  useEffect(() => setDirty(tabId, dirty), [dirty, tabId, setDirty]);

  const validate = useMemo(
    () => debounce((t: string) => api.post<ProfileValidation>("/api/profiles/validate", { text: t, name }).then(setV).catch(() => {}), 250),
    [name],
  );
  useEffect(() => {
    if (text !== null) validate(text);
  }, [text, validate]);

  const save = useCallback(async () => {
    if (text === null) return;
    try {
      await api.put(`/api/profiles/${enc(name)}`, { text });
      setSaved(text);
      useStore.getState().refreshProfiles();
      useStore.getState().toast("success", "Saved. Running labs pick it up when the link is re-applied or the lab restarts.");
    } catch (e) {
      toastError(e);
    }
  }, [text, name]);

  const doc = useMemo(() => (text === null ? null : YAML.parseDocument(text)), [text]);
  const data = useMemo(() => (doc && !doc.errors.length ? (doc.toJS() ?? {}) : {}) as Record<string, any>, [doc]);

  const set = (path: (string | number)[], value: unknown) => {
    if (!text) return;
    const d = YAML.parseDocument(text);
    if (d.errors.length) return;
    if (value === undefined || value === "") d.deleteIn(path);
    else d.setIn(path, value);
    // drop now-empty up/down maps
    for (const k of ["up", "down"]) {
      const m = d.get(k);
      if (m && typeof m === "object" && "items" in (m as object) && (m as { items: unknown[] }).items.length === 0) d.delete(k);
    }
    setText(d.toString({ lineWidth: 100 }));
  };

  const diagnostics = useMemo(() => {
    if (!doc || text === null) return [];
    if (doc.errors.length) return [{ line: doc.errors[0].linePos?.[0]?.line ?? 1, message: doc.errors[0].message.split("\n")[0] }];
    if (v && !v.ok) {
      const ds = v.details?.length ? v.details : [{ loc: "", msg: v.error ?? "invalid" }];
      return ds.map((d) => ({ line: lineOf(doc, text, d.loc) ?? 1, message: d.loc ? `${d.loc}: ${d.msg}` : d.msg }));
    }
    return [];
  }, [doc, v, text]);

  if (err) return <div className="banner error">{err}</div>;
  if (text === null) return <div className="empty">Loading…</div>;

  const rename = async () => {
    const to = await prompt({ title: "Rename profile", label: "New name", value: name, ok: "Rename" });
    if (!to || to === name) return;
    try {
      if (dirty) await save();
      await api.post(`/api/profiles/${enc(name)}/rename`, { to });
      await useStore.getState().refreshProfiles();
      useStore.getState().renameTab("profile", name, to);
      useStore.getState().toast("info", "Labs referencing the old name must be updated.");
    } catch (e) {
      toastError(e);
    }
  };
  const remove = async () => {
    if (!confirm(`Delete profile ${name}?`)) return;
    try {
      await api.del(`/api/profiles/${enc(name)}`);
      setDirty(tabId, false);
      useStore.getState().closeTab(tabId);
      await useStore.getState().refreshProfiles();
    } catch (e) {
      toastError(e);
    }
  };

  const completions = ({ key }: { key: string | null }) => {
    if (key === "distribution") return DISTS.map((label) => ({ label }));
    if (key === "extends") return profiles.filter((p) => p.name !== name).map((p) => ({ label: p.name }));
    return [];
  };

  const form = (
    <div className="scroll">
      <div className="col" style={{ padding: 14, gap: 14 }}>
        <div className="fields">
          <label className="field full"><span>Title</span><Lazy value={data.title} onCommit={(x) => set(["title"], x)} /></label>
          <label className="field full"><span>Description</span><Lazy value={data.description} onCommit={(x) => set(["description"], x)} /></label>
          <label className="field"><span>Category</span><Lazy value={data.category} list="netsim-cats" onCommit={(x) => set(["category"], x)} /></label>
          <label className="field">
            <span>Extends</span>
            <select value={data.extends ?? ""} onChange={(e) => set(["extends"], e.target.value || undefined)}>
              <option value="">—</option>
              {profiles.filter((p) => p.name !== name).map((p) => <option key={p.name}>{p.name}</option>)}
            </select>
          </label>
          <datalist id="netsim-cats">
            {[...new Set(profiles.map((p) => p.category).filter(Boolean))].map((c) => <option key={c!} value={c!} />)}
          </datalist>
        </div>

        <div className="card">
          <div className="card-head">Link behaviour <span className="faint" style={{ fontWeight: 400 }}>· one-way, per direction</span></div>
          <table className="grid compact">
            <thead>
              <tr>
                <th></th>
                <th>both</th>
                <th className="up">up (a→b)</th>
                <th className="down">down (b→a)</th>
              </tr>
            </thead>
            <tbody>
              {[
                ["delay", "40ms"],
                ["jitter", "10ms"],
                ["rate", "10mbit"],
                ["queue", "200ms or 100"],
                ["loss", "0.5%"],
                ["duplicate", "0%"],
                ["corrupt", "0%"],
                ["reorder", "0%"],
              ].map(([k, ph]) => (
                <tr key={k}>
                  <td className="muted">{k}</td>
                  <td><Lazy mono value={data[k]} placeholder={ph} onCommit={(x) => set([k], x || undefined)} /></td>
                  <td><Lazy mono value={data.up?.[k]} onCommit={(x) => set(["up", k], x || undefined)} /></td>
                  <td><Lazy mono value={data.down?.[k]} onCommit={(x) => set(["down", k], x || undefined)} /></td>
                </tr>
              ))}
              <tr>
                <td className="muted">distribution</td>
                <td colSpan={3}>
                  <select value={data.distribution ?? ""} onChange={(e) => set(["distribution"], e.target.value || undefined)}>
                    <option value="">uniform (default)</option>
                    {DISTS.slice(1).map((d) => <option key={d}>{d}</option>)}
                  </select>
                </td>
              </tr>
            </tbody>
          </table>
          <div className="card-body faint" style={{ fontSize: 11 }}>
            Rates are bits/s (<code>mbit</code> = <code>mbps</code> = 10⁶). Delay is the mean, jitter the standard deviation for non-uniform
            distributions. More fields (burst_loss, slot, ecn, timeline…) in the YAML; see the Guide tab.
          </div>
        </div>

        <DelayHistogram delay={data.delay} jitter={data.jitter} dist={data.distribution} />

        {v?.ok && v.preview && (
          <div className="card">
            <div className="card-head">Result</div>
            <div className="card-body col">
              {(["up", "down"] as const).map((d) => (
                <div key={d}>
                  <div className={d}>{d === "up" ? "a → b" : "b → a"}: <b>{v.preview![d].summary}</b></div>
                  <pre className="snippet mono" style={{ whiteSpace: "pre-wrap", margin: "4px 0 0" }}>{v.preview![d].tc}</pre>
                </div>
              ))}
            </div>
          </div>
        )}
        {v?.ok && v.steps && v.steps.length > 0 && (
          <div className="card">
            <div className="card-head">Timeline <span className="faint" style={{ fontWeight: 400 }}>{data.timeline?.loop === false ? "· runs once" : "· loops"}</span></div>
            <div className="card-body col">
              <div className="timeline-bar">
                {v.steps.map((s, i) => (
                  <div key={i} className={s.up.summary === "blackhole" ? "cut" : ""} style={{ flex: seconds(s.duration) }} title={s.up.summary}>
                    {s.label ?? i + 1}
                  </div>
                ))}
              </div>
              <table className="grid compact">
                <tbody>
                  {v.steps.map((s, i) => (
                    <tr key={i}>
                      <td>{s.label ?? `step ${i + 1}`}</td>
                      <td className="mono">{s.duration}</td>
                      <td className="mono" style={{ fontSize: 11, whiteSpace: "normal" }}>{s.up.summary}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        <div className="card">
          <div className="card-head">Try it</div>
          <div className="card-body">
            <div className="muted">Run one command through this profile:</div>
            <Snippet text={`netsim run -p ${name} -- curl -sI https://example.com`} />
            <div className="muted">Put it between a local client and server (port 6000 → 5000):</div>
            <Snippet text={`netsim pipe 6000:5000 -p ${name}`} />
          </div>
        </div>
      </div>
    </div>
  );

  return (
    <>
      <div className="toolbar">
        <h2 style={{ cursor: "pointer" }} onClick={rename} title="Rename">{name}</h2>
        {data.category && <span className="badge">{data.category}</span>}
        {v && !v.ok && <span className="badge danger" title={v.error}>invalid</span>}
        <span className="grow" />
        {dirty && (
          <>
            <button className="btn ghost" onClick={() => setText(saved)}><Undo2 size={14} /></button>
            <button className="btn primary" onClick={save}><Save size={14} /> Save</button>
          </>
        )}
        <button className="btn" title="Duplicate" onClick={() => createProfile(text, `${name}-copy`)}><Files size={14} /> Duplicate</button>
        <button className="icon-btn" title="Delete" onClick={remove}><Trash2 size={15} /></button>
      </div>
      {v && !v.ok && <div className="banner error">{v.error}</div>}
      <Split
        id="profile"
        initial={0.5}
        left={form}
        right={
          <>
            <div className="pane-head"><b>profiles/{name}.yaml</b></div>
            <div style={{ flex: 1, minHeight: 0 }}>
              <CodeEditor value={text} onChange={setText} diagnostics={diagnostics} completions={completions} onSave={save} />
            </div>
          </>
        }
      />
    </>
  );
}

function Snippet({ text }: { text: string }) {
  return (
    <div className="row" style={{ alignItems: "stretch" }}>
      <pre className="snippet mono grow" style={{ margin: "4px 0 10px" }}>{text}</pre>
      <button className="icon-btn" style={{ marginTop: 6 }} title="Copy" onClick={() => navigator.clipboard.writeText(text)}><Copy size={14} /></button>
    </div>
  );
}

function seconds(s: string) {
  const m = /^([\d.]+)\s*(us|ms|s|min)?$/.exec(String(s).trim());
  if (!m) return 1;
  const v = Number(m[1]);
  return m[2] === "ms" ? v / 1000 : m[2] === "us" ? v / 1e6 : m[2] === "min" ? v * 60 : v;
}

function ms(v: unknown): number {
  if (v == null || v === "") return 0;
  if (typeof v === "number") return v;
  const m = /^([\d.]+)\s*(us|ms|s)?$/.exec(String(v).trim());
  if (!m) return 0;
  const n = Number(m[1]);
  return m[2] === "s" ? n * 1000 : m[2] === "us" ? n / 1000 : n;
}

// standardised samplers matching the tables netsim generates / iproute2 ships
function gauss() {
  const u = 1 - Math.random();
  return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * Math.random());
}
const SAMPLERS: Record<string, () => number> = {
  normal: gauss,
  exponential: () => -Math.log(1 - Math.random()) - 1,
  lognormal: () => {
    const s = 0.6;
    const mean = Math.exp((s * s) / 2);
    const sd = Math.sqrt((Math.exp(s * s) - 1) * Math.exp(s * s));
    return (Math.exp(s * gauss()) - mean) / sd;
  },
  weibull: () => {
    const k = 1.5;
    const g1 = 0.9027, g2 = 1.1906; // Γ(1+1/k), Γ(1+2/k)
    return (Math.pow(-Math.log(1 - Math.random()), 1 / k) - g1) / Math.sqrt(g2 - g1 * g1);
  },
  pareto: () => {
    const a = 3; // heavy tail, standardised
    const x = 1 / Math.pow(1 - Math.random(), 1 / a);
    const mean = a / (a - 1);
    const sd = Math.sqrt(a / ((a - 1) ** 2 * (a - 2)));
    return (x - mean) / sd;
  },
};
SAMPLERS.paretonormal = () => (Math.random() < 0.25 ? gauss() : SAMPLERS.pareto());

function DelayHistogram({ delay, jitter, dist }: { delay: unknown; jitter: unknown; dist?: string }) {
  const d = ms(delay);
  const j = ms(jitter);
  const bins = useMemo(() => {
    if (!j) return null;
    const n = 20000;
    const xs: number[] = [];
    const sampler = SAMPLERS[dist ?? ""];
    for (let i = 0; i < n; i++) {
      const z = sampler ? Math.max(-4, Math.min(4, sampler())) : Math.random() * 2 - 1;
      xs.push(Math.max(0, d + j * z));
    }
    xs.sort((a, b) => a - b);
    const lo = xs[0];
    const hi = xs[Math.floor(n * 0.995)];
    const count = 48;
    const w = (hi - lo) / count || 1;
    const h = new Array(count).fill(0);
    for (const x of xs) if (x <= hi) h[Math.min(count - 1, Math.floor((x - lo) / w))]++;
    return { h, lo, hi, p50: xs[n >> 1], p99: xs[Math.floor(n * 0.99)] };
  }, [d, j, dist]);
  if (!bins) return null;
  const max = Math.max(...bins.h);
  return (
    <div className="card">
      <div className="card-head">One-way delay distribution <span className="faint" style={{ fontWeight: 400 }}>· simulated, before rate/queueing</span></div>
      <div className="card-body">
        <svg viewBox="0 0 480 110" style={{ width: "100%", height: 120 }} preserveAspectRatio="none">
          {bins.h.map((v, i) => (
            <rect key={i} x={i * 10 + 1} width={8} y={100 - (v / max) * 96} height={(v / max) * 96} rx={1.5} fill="var(--accent)" opacity={0.75} />
          ))}
          <line x1="0" x2="480" y1="100.5" y2="100.5" stroke="var(--border-strong)" />
        </svg>
        <div className="row faint" style={{ fontSize: 11, justifyContent: "space-between" }}>
          <span>{bins.lo.toFixed(1)} ms</span>
          <span>median {bins.p50.toFixed(1)} ms · p99 {bins.p99.toFixed(1)} ms</span>
          <span>{bins.hi.toFixed(1)} ms</span>
        </div>
      </div>
    </div>
  );
}
