// SPDX-License-Identifier: LGPL-3.0-or-later
// Copyright (C) 2026 Eduardo Correia
import {
  Activity,
  FileText,
  ListTree,
  Network,
  Play,
  RotateCw,
  Save,
  ScrollText,
  Square,
  SquareTerminal,
  Undo2,
  Waves,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, ApiError, enc } from "../lib/api";
import { debounce } from "../lib/format";
import { lineOf, parseLab } from "../lib/labdoc";
import { toastError, useStore } from "../lib/store";
import type { LabEvent, Plan, Runtime, StatsSample, ValidationDetail } from "../lib/types";
import { useReconnect, useTopic } from "../lib/ws";
import { labAction } from "../components/Sidebar";
import { prompt } from "../components/ui";
import Design from "./lab/Design";
import Monitor from "./lab/Monitor";
import Traffic from "./lab/Traffic";
import Programs from "./lab/Programs";
import Terminals from "./lab/Terminals";
import Commands from "./lab/Commands";

export type LabTab = "design" | "monitor" | "traffic" | "programs" | "terminal" | "commands";

export interface Validation {
  ok: boolean;
  plan?: Plan;
  error?: string;
  details?: ValidationDetail[];
}

const MAX_SAMPLES = 600;

export default function LabView({ name, tabId, visible }: { name: string; tabId: string; visible: boolean }) {
  const lab = useStore((s) => s.labs.find((l) => l.name === name));
  const setDirty = useStore((s) => s.setDirty);
  const [saved, setSaved] = useState<string | null>(null);
  const [text, setText] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [validation, setValidation] = useState<Validation | null>(null);
  const [runtime, setRuntime] = useState<Runtime | null>(null);
  const [samples, setSamples] = useState<StatsSample[]>([]);
  const [events, setEvents] = useState<LabEvent[]>([]);
  const [busy, setBusy] = useState(false);
  const running = lab?.state === "running";
  const [view, setView] = useState<LabTab>(() => (localStorage.getItem(`netsim.labview.${name}`) as LabTab) || "design");

  const chooseView = (v: LabTab) => {
    setView(v);
    localStorage.setItem(`netsim.labview.${name}`, v);
  };

  // ---------------------------------------------------------------- source
  // the last text known to be on disk; a ref so `load` never compares against a stale value
  const savedRef = useRef<string | null>(null);

  /** (Re)read the file. Unsaved edits are kept; otherwise the editor follows the file. */
  const load = useCallback(async () => {
    try {
      const r = await api.get<{ text: string }>(`/api/labs/${enc(name)}`);
      const previous = savedRef.current;
      savedRef.current = r.text;
      setSaved(r.text);
      setText((cur) => (cur === null || cur === previous ? r.text : cur));
      setLoadError(null);
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : String(e));
    }
  }, [name]);

  useEffect(() => {
    load();
  }, [load]);
  // the file can change outside this tab (CLI, "Save to lab", another tab or editor)
  useTopic("labs", () => load());

  const dirty = text !== null && saved !== null && text !== saved;
  useEffect(() => setDirty(tabId, dirty), [dirty, tabId, setDirty]);

  const validate = useMemo(
    () =>
      debounce(async (t: string) => {
        try {
          setValidation(await api.post<Validation>("/api/labs/validate", { text: t, name }));
        } catch (e) {
          setValidation({ ok: false, error: e instanceof Error ? e.message : String(e) });
        }
      }, 350),
    [name],
  );
  useEffect(() => {
    if (text !== null) validate(text);
  }, [text, validate]);

  const save = useCallback(async () => {
    if (text === null) return;
    try {
      const r = await api.put<{ live: string[]; restart: boolean }>(`/api/labs/${enc(name)}`, { text });
      savedRef.current = text;
      setSaved(text);
      const toast = useStore.getState().toast;
      if (r.restart) toast("info", "Saved. Nodes, wiring, programs or proxies changed: restart the lab to apply.");
      else if (r.live.length) toast("success", `Saved and applied live: ${r.live.join(", ")}`);
      else toast("success", "Saved");
    } catch (e) {
      toastError(e);
    }
  }, [text, name, running]);

  // ---------------------------------------------------------------- runtime
  const fetchRuntime = useCallback(async () => {
    try {
      setRuntime(await api.get<Runtime>(`/api/labs/${enc(name)}/runtime`));
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) setRuntime(null);
    }
  }, [name]);

  useEffect(() => {
    if (!running) {
      setRuntime(null);
      return;
    }
    fetchRuntime();
    api.get<StatsSample[]>(`/api/labs/${enc(name)}/history`).then((h) => setSamples(h.slice(-MAX_SAMPLES)));
    api.get<LabEvent[]>(`/api/labs/${enc(name)}/events`).then(setEvents);
  }, [running, name, fetchRuntime]);

  const refetchSoon = useMemo(() => debounce(fetchRuntime, 150), [fetchRuntime]);
  useTopic(running ? `lab:${name}:runtime` : null, refetchSoon);
  useTopic(`lab:${name}:event`, (ev: LabEvent) => setEvents((xs) => [...xs.slice(-499), ev]));
  useTopic(running && visible ? `lab:${name}:stats` : null, (s: StatsSample) =>
    setSamples((xs) => (xs.length >= MAX_SAMPLES ? [...xs.slice(1), s] : [...xs, s])),
  );
  useReconnect(() => {
    if (running) fetchRuntime();
  });

  // keep collecting while hidden is not needed; on return refill from history
  const wasVisible = useRef(visible);
  useEffect(() => {
    if (visible && !wasVisible.current && running) {
      api.get<StatsSample[]>(`/api/labs/${enc(name)}/history`).then((h) => setSamples(h.slice(-MAX_SAMPLES)));
    }
    wasVisible.current = visible;
  }, [visible, running, name]);

  // ---------------------------------------------------------------- actions
  const act = async (action: "up" | "down" | "restart") => {
    setBusy(true);
    try {
      if (dirty && action !== "down") await save();
      await labAction(name, action);
      if (action !== "down") chooseView("monitor");
    } finally {
      setBusy(false);
    }
  };

  const rename = async () => {
    const to = await prompt({ title: "Rename lab", label: "New name", value: name, ok: "Rename" });
    if (!to || to === name) return;
    try {
      if (dirty) await save();
      await api.post(`/api/labs/${enc(name)}/rename`, { to });
      await useStore.getState().refreshLabs();
      useStore.getState().renameTab("lab", name, to);
    } catch (e) {
      toastError(e);
    }
  };

  const remove = async () => {
    if (!confirm(`Delete lab ${name}? This removes labs/${name}.yaml.`)) return;
    try {
      await api.del(`/api/labs/${enc(name)}`);
      setDirty(tabId, false);
      useStore.getState().closeTab(tabId);
      await useStore.getState().refreshLabs();
    } catch (e) {
      toastError(e);
    }
  };

  const parsed = useMemo(() => (text === null ? null : parseLab(text)), [text]);
  const diagnostics = useMemo(() => {
    if (!parsed || text === null) return [];
    if (parsed.error) return [{ line: parsed.error.line ?? 1, message: parsed.error.msg }];
    if (validation && !validation.ok) {
      const ds = validation.details?.length ? validation.details : [{ loc: "", msg: validation.error ?? "invalid" }];
      return ds.map((d) => ({ line: lineOf(parsed.doc, text, d.loc) ?? 1, message: d.loc ? `${d.loc}: ${d.msg}` : d.msg }));
    }
    return [];
  }, [parsed, validation, text]);

  if (loadError) return <div className="banner error">{loadError}</div>;
  if (text === null || !parsed) return <div className="empty">Loading…</div>;

  const plan = runtime?.plan ?? validation?.plan ?? null;
  const tabs: { id: LabTab; label: string; icon: typeof Network; live?: boolean }[] = [
    { id: "design", label: "Design", icon: Network },
    { id: "monitor", label: "Monitor", icon: Activity, live: true },
    { id: "traffic", label: "Traffic", icon: Waves, live: true },
    { id: "programs", label: "Programs", icon: ListTree, live: true },
    { id: "terminal", label: "Terminal", icon: SquareTerminal, live: true },
    { id: "commands", label: "Commands", icon: ScrollText, live: true },
  ];
  const effectiveView = !running && tabs.find((t) => t.id === view)?.live ? "design" : view;

  return (
    <>
      <div className="toolbar">
        <h2 style={{ cursor: "pointer" }} onClick={rename} title="Rename">
          {name}
        </h2>
        <span className={`badge ${running ? "ok" : lab?.state === "failed" ? "danger" : lab?.state === "stopped" ? "" : "warn"}`}>
          <span className={`dot ${lab?.state}`} /> {lab?.state ?? "unknown"}
        </span>
        {runtime?.modified && <span className="badge warn" title="The file changed since the lab started">file changed · restart to apply</span>}
        {lab?.ephemeral && <span className="badge">temporary</span>}
        <div className="seg" style={{ marginLeft: 8 }}>
          {tabs.map((t) => (
            <button key={t.id} className={effectiveView === t.id ? "on" : ""} disabled={t.live && !running} onClick={() => chooseView(t.id)}
              title={t.live && !running ? "Start the lab first" : ""}>
              <t.icon size={13} /> {t.label}
            </button>
          ))}
        </div>
        <div className="grow" />
        {dirty && (
          <>
            <button className="btn ghost" onClick={() => setText(saved)} title="Discard changes">
              <Undo2 size={14} />
            </button>
            <button className="btn" onClick={save} title="Save (Ctrl+S)">
              <Save size={14} /> Save
            </button>
          </>
        )}
        {running ? (
          <>
            <button className="btn" disabled={busy} onClick={() => act("restart")} title="Stop and start again, picking up file changes">
              <RotateCw size={14} /> Restart
            </button>
            <button className="btn danger" disabled={busy} onClick={() => act("down")}>
              <Square size={14} /> Stop
            </button>
          </>
        ) : (
          <button className="btn primary" disabled={busy || (validation ? !validation.ok : false) || lab?.state === "starting"}
            onClick={() => act("up")}>
            <Play size={14} /> {busy ? "Starting…" : "Start"}
          </button>
        )}
        <button className="icon-btn" title="Delete lab" onClick={remove}>
          <FileText size={15} />
        </button>
      </div>
      {lab?.state === "failed" && lab.error && <div className="banner error">Start failed: {lab.error}</div>}
      {effectiveView === "design" && (
        <Design
          name={name}
          text={text}
          parsed={parsed}
          plan={plan}
          validation={validation}
          diagnostics={diagnostics}
          onChange={setText}
          onSave={save}
          running={running}
        />
      )}
      {effectiveView === "monitor" && runtime && (
        <Monitor name={name} data={parsed.data} runtime={runtime} samples={samples} events={events}
          editorDirty={dirty} onFileSaved={load} />
      )}
      {effectiveView === "traffic" && runtime && <Traffic name={name} runtime={runtime} />}
      {effectiveView === "programs" && runtime && <Programs name={name} runtime={runtime} />}
      {runtime && (
        // kept mounted so shells survive switching views
        <div style={{ display: effectiveView === "terminal" ? "flex" : "none", flexDirection: "column", flex: 1, minHeight: 0 }}>
          <Terminals name={name} runtime={runtime} visible={visible && effectiveView === "terminal"} />
        </div>
      )}
      {effectiveView === "commands" && runtime && <Commands name={name} />}
      {effectiveView !== "design" && !runtime && <div className="empty">Loading runtime…</div>}
    </>
  );
}
