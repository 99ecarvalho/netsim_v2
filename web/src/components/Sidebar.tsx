// SPDX-License-Identifier: LGPL-3.0-or-later
// Copyright (C) 2026 Eduardo Correia
import { ChevronDown, ChevronRight, CircleHelp, Clock, Play, Plus, Square } from "lucide-react";
import { useMemo, useState } from "react";
import { api, enc } from "../lib/api";
import { NEW_LAB, NEW_PROFILE } from "../lib/labdoc";
import { toastError, useStore } from "../lib/store";
import { prompt } from "./ui";

const NAME_RE = /^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,47}$/;

export async function createLab(text = NEW_LAB, suggested = "") {
  const name = await prompt({ title: "New lab", label: "Name", value: suggested, placeholder: "my-lab", ok: "Create" });
  if (!name) return;
  if (!NAME_RE.test(name)) return toastError("Use letters, digits, '-', '_' or '.'");
  const s = useStore.getState();
  if (s.labs.some((l) => l.name === name)) return toastError(`Lab ${name} already exists`);
  try {
    await api.put(`/api/labs/${enc(name)}`, { text });
    await s.refreshLabs();
    s.openTab("lab", name);
  } catch (e) {
    toastError(e);
  }
}

export async function createProfile(text = NEW_PROFILE, suggested = "") {
  const name = await prompt({ title: "New profile", label: "Name", value: suggested, placeholder: "my-link", ok: "Create" });
  if (!name) return;
  if (!NAME_RE.test(name)) return toastError("Use letters, digits, '-', '_' or '.'");
  const s = useStore.getState();
  if (s.profiles.some((p) => p.name === name)) return toastError(`Profile ${name} already exists`);
  try {
    await api.put(`/api/profiles/${enc(name)}`, { text });
    await s.refreshProfiles();
    s.openTab("profile", name);
  } catch (e) {
    toastError(e);
  }
}

export async function labAction(name: string, action: "up" | "down" | "restart") {
  try {
    await api.post(`/api/labs/${enc(name)}/${action}`);
  } catch (e) {
    toastError(e);
  }
}

export default function Sidebar() {
  const labs = useStore((s) => s.labs);
  const profiles = useStore((s) => s.profiles);
  const active = useStore((s) => s.active);
  const openTab = useStore((s) => s.openTab);
  const [q, setQ] = useState("");
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>(() =>
    JSON.parse(localStorage.getItem("netsim.sidebar.collapsed") || "{}"),
  );

  const toggle = (cat: string) => {
    const next = { ...collapsed, [cat]: !collapsed[cat] };
    setCollapsed(next);
    localStorage.setItem("netsim.sidebar.collapsed", JSON.stringify(next));
  };

  const groups = useMemo(() => {
    const needle = q.toLowerCase();
    const out = new Map<string, typeof profiles>();
    for (const p of profiles) {
      const hay = `${p.name} ${p.title ?? ""} ${p.category ?? ""} ${(p.tags ?? []).join(" ")}`.toLowerCase();
      if (needle && !hay.includes(needle)) continue;
      const cat = p.category || "other";
      out.set(cat, [...(out.get(cat) ?? []), p]);
    }
    return [...out.entries()].sort(([a], [b]) => a.localeCompare(b));
  }, [profiles, q]);

  return (
    <aside className="sidebar">
      <div className="side-section">
        <div className="side-head">
          Labs
          <button className="icon-btn sm" title="New lab" onClick={() => createLab()}>
            <Plus size={14} />
          </button>
        </div>
        {labs.map((l) => (
          <div
            key={l.name}
            className={`side-item ${active === `lab:${l.name}` ? "active" : ""}`}
            onClick={() => openTab("lab", l.name)}
            title={l.error ?? l.description ?? ""}
          >
            <span className={`dot ${l.state}`} />
            <span className="name">{l.name}</span>
            {l.ephemeral && <span className="meta">temp</span>}
            {l.invalid && <span className="badge danger">invalid</span>}
            <span className="hover-only" onClick={(e) => e.stopPropagation()}>
              {l.state === "running" ? (
                <button className="icon-btn sm" title="Stop" onClick={() => labAction(l.name, "down")}>
                  <Square size={12} />
                </button>
              ) : (
                <button className="icon-btn sm" title="Start" onClick={() => labAction(l.name, "up")}>
                  <Play size={12} />
                </button>
              )}
            </span>
          </div>
        ))}
        {!labs.length && <div className="side-item faint">no labs yet</div>}
      </div>

      <div className="side-section" style={{ flex: 1 }}>
        <div className="side-head">
          Profiles <span className="faint" style={{ textTransform: "none", letterSpacing: 0 }}>{profiles.length}</span>
          <button className="icon-btn sm" title="New profile" onClick={() => createProfile()}>
            <Plus size={14} />
          </button>
        </div>
        <input className="side-search" placeholder="Filter profiles…" value={q} onChange={(e) => setQ(e.target.value)} />
        {groups.map(([cat, items]) => (
          <div key={cat}>
            <div className="side-group" onClick={() => toggle(cat)}>
              {collapsed[cat] && !q ? <ChevronRight size={12} /> : <ChevronDown size={12} />}
              {cat} <span className="faint">{items.length}</span>
            </div>
            {(!collapsed[cat] || q) &&
              items.map((p) => (
                <div
                  key={p.name}
                  className={`side-item ${active === `profile:${p.name}` ? "active" : ""}`}
                  onClick={() => openTab("profile", p.name)}
                  title={p.summary ? `up: ${p.summary.up}\ndown: ${p.summary.down}` : p.error ?? ""}
                  style={{ paddingLeft: 26 }}
                >
                  <span className="name">{p.name}</span>
                  {!!p.timeline && <Clock size={12} className="faint" />}
                  {p.error && <span className="badge danger">!</span>}
                </div>
              ))}
          </div>
        ))}
      </div>

      <div className="side-section" style={{ borderTop: "1px solid var(--border)" }}>
        <div className="side-item" onClick={() => openTab("help")}>
          <CircleHelp size={14} className="muted" />
          <span className="name">Guide &amp; reference</span>
        </div>
      </div>
    </aside>
  );
}
