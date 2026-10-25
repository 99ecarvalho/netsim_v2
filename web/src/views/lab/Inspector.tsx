import { ArrowLeftRight, Plus, Trash2, X } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import type { Document } from "yaml";
import { NODE_ICONS } from "../../components/icons";
import type { Selection } from "../../components/Topology";
import { appendItem, removeItem, removeLink, removeNode, renameNode, setIn, setNodeField } from "../../lib/labdoc";
import { useStore } from "../../lib/store";
import type { LabSpec, LinkSpec, NodeKind, Plan } from "../../lib/types";

interface Props {
  lab: string;
  data: LabSpec;
  plan: Plan | null;
  selection: NonNullable<Selection>;
  onSelect: (s: Selection) => void;
  mutate: (fn: (doc: Document) => void) => void;
  running: boolean;
}

/** Text input that commits on blur/Enter, so typing doesn't rewrite YAML per keystroke. */
export function Lazy({ value, onCommit, placeholder, mono, list }: {
  value: string | number | undefined;
  onCommit: (v: string) => void;
  placeholder?: string;
  mono?: boolean;
  list?: string;
}) {
  const [v, setV] = useState(value ?? "");
  useEffect(() => setV(value ?? ""), [value]);
  return (
    <input
      value={v}
      list={list}
      placeholder={placeholder}
      className={mono ? "mono" : ""}
      onChange={(e) => setV(e.target.value)}
      onBlur={() => String(v) !== String(value ?? "") && onCommit(String(v).trim())}
      onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()}
    />
  );
}

function Field({ label, children, hint, full }: { label: string; children: ReactNode; hint?: string; full?: boolean }) {
  return (
    <label className={`field ${full ? "full" : ""}`}>
      <span>{label}</span>
      {children}
      {hint && <span className="hint">{hint}</span>}
    </label>
  );
}

export default function Inspector({ data, plan, selection, onSelect, mutate, running }: Props) {
  return (
    <div className="inspector">
      <div className="card-head">
        <span className="grow">{selection.type === "node" ? `Node · ${selection.id}` : `Link · ${selection.id}`}</span>
        <button className="icon-btn sm" onClick={() => onSelect(null)}>
          <X size={14} />
        </button>
      </div>
      <div className="body">
        {running && (
          <div className="badge warn" style={{ whiteSpace: "normal" }}>
            Lab is running: topology edits apply after save + restart. Use Monitor for live link changes.
          </div>
        )}
        {selection.type === "node" ? (
          <NodeInspector name={selection.id} data={data} plan={plan} mutate={mutate} onSelect={onSelect} />
        ) : (
          <LinkInspector index={selection.index} data={data} plan={plan} mutate={mutate} onSelect={onSelect} />
        )}
      </div>
    </div>
  );
}

function NodeInspector({ name, data, plan, mutate, onSelect }: {
  name: string;
  data: LabSpec;
  plan: Plan | null;
  mutate: Props["mutate"];
  onSelect: Props["onSelect"];
}) {
  const spec = data.nodes?.[name] ?? {};
  if (!data.nodes || !(name in data.nodes)) return <div className="faint">Node no longer exists.</div>;
  const kind = (spec.kind ?? "host") as NodeKind | "container";
  const pn = plan?.nodes[name];
  const programs = (data.programs ?? []).map((p, i) => ({ ...p, i })).filter((p) => p.node === name);
  const set = (key: string, value: unknown) => mutate((d) => setNodeField(d, name, key, value));
  const hasExternal = Object.entries(data.nodes).some(([n, s]) => n !== name && s?.kind === "external");

  return (
    <>
      <div className="fields">
        <Field label="Name">
          <Lazy value={name} onCommit={(v) => {
            if (!v || v === name) return;
            if (!/^[a-zA-Z][a-zA-Z0-9-]{0,23}$/.test(v)) return useStore.getState().toast("error", "Node names: letters, digits, '-', start with a letter");
            if (data.nodes?.[v]) return useStore.getState().toast("error", `${v} exists`);
            mutate((d) => renameNode(d, name, v));
            onSelect({ type: "node", id: v });
          }} />
        </Field>
        <Field label="Kind">
          <select value={kind} onChange={(e) => set("kind", e.target.value === "host" ? undefined : e.target.value)}>
            <option value="host">host</option>
            <option value="router">router</option>
            <option value="switch">switch</option>
            <option value="external" disabled={hasExternal}>external (this machine)</option>
            <option value="container">container (docker)</option>
          </select>
        </Field>
        {kind !== "switch" && kind !== "external" && (
          <Field label="Icon">
            <select value={spec.icon ?? ""} onChange={(e) => set("icon", e.target.value || undefined)}>
              <option value="">default</option>
              {Object.keys(NODE_ICONS).map((k) => (
                <option key={k}>{k}</option>
              ))}
            </select>
          </Field>
        )}
        {kind === "external" && (
          <Field label="Internet" hint="NAT lab traffic out of this machine">
            <select value={spec.internet ? "yes" : "no"} onChange={(e) => set("internet", e.target.value === "yes")}>
              <option value="no">no</option>
              <option value="yes">yes</option>
            </select>
          </Field>
        )}
        {kind === "switch" && (
          <Field label="Subnet" hint="optional CIDR">
            <Lazy mono value={spec.subnet} placeholder="auto" onCommit={(v) => set("subnet", v || undefined)} />
          </Field>
        )}
        {kind === "container" && (
          <Field label="Container" hint="docker name or id (must be running)">
            <Lazy mono value={(spec as Record<string, string>).container} placeholder="my-api" onCommit={(v) => set("container", v || undefined)} />
          </Field>
        )}
        <Field label="Description" full>
          <Lazy value={spec.description} placeholder="optional" onCommit={(v) => set("description", v || undefined)} />
        </Field>
      </div>

      {pn && pn.ifaces.length > 0 && (
        <div>
          <div className="field"><span>Interfaces</span></div>
          <table className="grid compact">
            <tbody>
              {pn.ifaces.map((i) => (
                <tr key={i.name}>
                  <td className="mono">{i.name}</td>
                  <td className="mono">{i.ip ?? "—"}</td>
                  <td className="faint">→ {i.peer}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {pn && pn.routes.length > 0 && (
        <div>
          <div className="field"><span>Routes</span></div>
          <table className="grid compact">
            <tbody>
              {pn.routes.map((r) => (
                <tr key={r.dst}>
                  <td className="mono">{r.dst}</td>
                  <td className="mono faint">via {r.via}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {kind !== "switch" && (
        <div className="col" style={{ gap: 6 }}>
          <div className="row">
            <div className="field grow"><span>Programs</span></div>
            <button className="btn small" onClick={() => {
              const used = new Set((data.programs ?? []).map((p) => p.name));
              let n = 1;
              while (used.has(`${name}-app${n}`)) n++;
              mutate((d) => appendItem(d, "programs", { name: `${name}-app${n}`, node: name, cmd: "echo hello from $NETSIM_NODE; sleep infinity", autostart: false }));
            }}>
              <Plus size={12} /> Add
            </button>
          </div>
          {programs.map((p) => (
            <div key={p.i} className="card" style={{ padding: 8, display: "flex", flexDirection: "column", gap: 6 }}>
              <div className="row">
                <Lazy value={p.name} onCommit={(v) => v && mutate((d) => setIn(d, ["programs", p.i, "name"], v))} />
                <label className="row" style={{ gap: 4, fontSize: 12 }}>
                  <input type="checkbox" checked={!!p.autostart}
                    onChange={(e) => mutate((d) => setIn(d, ["programs", p.i, "autostart"], e.target.checked || undefined))} />
                  autostart
                </label>
                <button className="icon-btn sm" title="Remove" onClick={() => mutate((d) => removeItem(d, "programs", p.i))}>
                  <Trash2 size={13} />
                </button>
              </div>
              <Lazy mono value={p.cmd} placeholder="command" onCommit={(v) => v && mutate((d) => setIn(d, ["programs", p.i, "cmd"], v))} />
            </div>
          ))}
        </div>
      )}

      <button className="btn danger" onClick={() => {
        mutate((d) => removeNode(d, name));
        onSelect(null);
      }}>
        <Trash2 size={14} /> Delete node and its links
      </button>
    </>
  );
}

const OVERRIDES = [
  { key: "delay", placeholder: "40ms" },
  { key: "jitter", placeholder: "10ms" },
  { key: "loss", placeholder: "1%" },
  { key: "rate", placeholder: "5mbit" },
];

function LinkInspector({ index, data, plan, mutate, onSelect }: {
  index: number;
  data: LabSpec;
  plan: Plan | null;
  mutate: Props["mutate"];
  onSelect: Props["onSelect"];
}) {
  const profiles = useStore((s) => s.profiles);
  const openTab = useStore((s) => s.openTab);
  const link = data.links?.[index] as LinkSpec | undefined;
  if (!link) return <div className="faint">Link no longer exists.</div>;
  const id = link.name || `${link.a}--${link.b}`;
  const pl = plan?.links[id];
  const set = (path: (string | number)[], v: unknown) => mutate((d) => setIn(d, ["links", index, ...path], v));
  const prof = profiles.find((p) => p.name === link.profile);
  const cats = [...new Set(profiles.map((p) => p.category || "other"))].sort();

  return (
    <>
      <div className="row">
        <span className="badge accent">a: {link.a}</span>
        <button className="icon-btn sm" title="Swap ends (up/down directions swap too)" onClick={() => mutate((d) => {
          d.setIn(["links", index, "a"], link.b);
          d.setIn(["links", index, "b"], link.a);
        })}>
          <ArrowLeftRight size={13} />
        </button>
        <span className="badge accent">b: {link.b}</span>
      </div>
      <Field label="Profile" hint={prof?.description ?? undefined}>
        <div className="row">
          <select className="grow" value={link.profile ?? ""} onChange={(e) => set(["profile"], e.target.value || undefined)}>
            <option value="">— none (ideal) —</option>
            {cats.map((c) => (
              <optgroup key={c} label={c}>
                {profiles.filter((p) => (p.category || "other") === c).map((p) => (
                  <option key={p.name} value={p.name}>
                    {p.name}{p.timeline ? " ⏱" : ""}
                  </option>
                ))}
              </optgroup>
            ))}
          </select>
          {link.profile && (
            <button className="btn small" onClick={() => openTab("profile", link.profile!)}>Open</button>
          )}
        </div>
      </Field>
      {pl?.shaping && (
        <div className="kv mono" style={{ fontSize: 11 }}>
          <dt className="up">a→b</dt>
          <dd>{pl.shaping.up}</dd>
          <dt className="down">b→a</dt>
          <dd>{pl.shaping.down}</dd>
        </div>
      )}

      <div>
        <div className="field"><span>Overrides on top of the profile</span></div>
        <table className="grid compact">
          <thead>
            <tr>
              <th></th>
              <th>both</th>
              <th className="up">a→b</th>
              <th className="down">b→a</th>
            </tr>
          </thead>
          <tbody>
            {OVERRIDES.map((o) => (
              <tr key={o.key}>
                <td className="muted">{o.key}</td>
                <td><Lazy mono value={link[o.key] as string} placeholder={o.placeholder} onCommit={(v) => set([o.key], v || undefined)} /></td>
                <td><Lazy mono value={link.up?.[o.key] as string} onCommit={(v) => set(["up", o.key], v || undefined)} /></td>
                <td><Lazy mono value={link.down?.[o.key] as string} onCommit={(v) => set(["down", o.key], v || undefined)} /></td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="hint faint" style={{ fontSize: 11, marginTop: 4 }}>Any profile field works in YAML (burst_loss, reorder, slot…).</div>
      </div>

      <div className="fields">
        <Field label="Name" hint="needed for parallel links">
          <Lazy value={link.name} placeholder={`${link.a}--${link.b}`} onCommit={(v) => {
            set(["name"], v || undefined);
            onSelect({ type: "link", id: v || `${link.a}--${link.b}`, index });
          }} />
        </Field>
        <Field label="MTU">
          <Lazy mono value={link.mtu} placeholder="1500" onCommit={(v) => set(["mtu"], v ? Number(v) : undefined)} />
        </Field>
        <Field label="Subnet" full>
          <Lazy mono value={link.subnet} placeholder={plan ? segmentOf(plan, id) : "auto"} onCommit={(v) => set(["subnet"], v || undefined)} />
        </Field>
        <Field label={`IP of ${link.a}`}>
          <Lazy mono value={link.ip_a} placeholder={pl?.a.ip ?? "auto"} onCommit={(v) => set(["ip_a"], v || undefined)} />
        </Field>
        <Field label={`IP of ${link.b}`}>
          <Lazy mono value={link.ip_b} placeholder={pl?.b.ip ?? "auto"} onCommit={(v) => set(["ip_b"], v || undefined)} />
        </Field>
      </div>
      <button className="btn danger" onClick={() => {
        mutate((d) => removeLink(d, index));
        onSelect(null);
      }}>
        <Trash2 size={14} /> Delete link
      </button>
    </>
  );
}

function segmentOf(plan: Plan, id: string) {
  const seg = plan.links[id]?.segment;
  return plan.segments.find((s) => s.index === seg)?.subnet ?? "auto";
}
