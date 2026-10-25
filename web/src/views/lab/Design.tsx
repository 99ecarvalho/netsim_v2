import { CheckCircle2, CircleAlert } from "lucide-react";
import YAML from "yaml";
import { useCallback, useState } from "react";
import CodeEditor, { type LineDiagnostic } from "../../components/CodeEditor";
import Topology, { type Selection } from "../../components/Topology";
import { Split } from "../../components/ui";
import { addLink, addNode, freeName, removeLink, removeNode, serialize, setLayout, type ParsedLab } from "../../lib/labdoc";
import { useStore } from "../../lib/store";
import type { NodeKind, Plan } from "../../lib/types";
import type { Validation } from "../LabView";
import Inspector from "./Inspector";

interface Props {
  name: string;
  text: string;
  parsed: ParsedLab;
  plan: Plan | null;
  validation: Validation | null;
  diagnostics: LineDiagnostic[];
  onChange: (t: string) => void;
  onSave: () => void;
  running: boolean;
}

export default function Design({ name, text, parsed, plan, validation, diagnostics, onChange, onSave, running }: Props) {
  const [selection, setSelection] = useState<Selection>(null);
  const profiles = useStore((s) => s.profiles);
  const data = parsed.data;

  /** Apply a mutation to the YAML document and push the new text. */
  const mutate = useCallback(
    (fn: (doc: ParsedLab["doc"]) => void) => {
      // fresh document each time so mutations never leak between edits
      const fresh = YAML.parseDocument(text);
      if (fresh.errors.length) return;
      fn(fresh);
      onChange(serialize(fresh));
    },
    [text, onChange],
  );

  const completions = useCallback(
    ({ key }: { key: string | null }) => {
      const nodeNames = Object.keys(data.nodes ?? {});
      switch (key) {
        case "profile":
          return profiles.map((p) => ({ label: p.name, detail: p.title ?? p.category ?? "" }));
        case "a":
        case "b":
        case "node":
        case "listen_node":
        case "target_node":
          return nodeNames.map((n) => ({ label: n }));
        case "kind":
          return ["host", "router", "switch", "external", "container"].map((label) => ({ label }));
        case "distribution":
          return ["uniform", "normal", "pareto", "paretonormal", "exponential", "lognormal", "weibull"].map((label) => ({ label }));
        case "restart":
          return ["no", "on-failure", "always"].map((label) => ({ label }));
        case "proto":
          return ["tcp", "udp"].map((label) => ({ label }));
        case "icon":
          return ["laptop", "desktop", "server", "phone", "iot", "cloud", "router", "database", "radio", "satellite", "building", "vehicle"].map((label) => ({ label }));
        default:
          return [];
      }
    },
    [data, profiles],
  );

  const status = parsed.error ? (
    <span className="err row"><CircleAlert size={13} /> YAML: {parsed.error.msg}</span>
  ) : validation && !validation.ok ? (
    <span className="err row" title={validation.error}><CircleAlert size={13} /> {validation.error}</span>
  ) : validation?.ok ? (
    <span className="row" style={{ color: "var(--ok)" }}>
      <CheckCircle2 size={13} /> valid · {Object.keys(data.nodes ?? {}).length} nodes · {(data.links ?? []).length} links · subnet {validation.plan?.base}
    </span>
  ) : (
    <span className="faint">validating…</span>
  );

  const graph = (
    <div style={{ position: "relative", flex: 1, minHeight: 0 }}>
      {parsed.error ? (
        <div className="empty">Fix the YAML to see the graph.</div>
      ) : (
        <Topology
          data={data}
          plan={plan}
          editable
          selection={selection}
          onSelect={setSelection}
          onMove={(pos) => mutate((d) => setLayout(d, pos))}
          onConnect={(a, b) => mutate((d) => {
            const idx = addLink(d, a, b);
            setTimeout(() => setSelection({ type: "link", id: `${a}--${b}`, index: idx }), 0);
          })}
          onAdd={(kind: NodeKind, pos) => mutate((d) => {
            const n = freeName(data, kind);
            addNode(d, n, kind, pos);
            setTimeout(() => setSelection({ type: "node", id: n }), 0);
          })}
          onDelete={(nodes, links) => mutate((d) => {
            [...links].sort((x, y) => y - x).forEach((i) => removeLink(d, i));
            nodes.forEach((n) => removeNode(d, n));
            setSelection(null);
          })}
        />
      )}
      {selection && !parsed.error && (
        <Inspector
          lab={name}
          data={data}
          plan={plan}
          selection={selection}
          onSelect={setSelection}
          mutate={mutate}
          running={running}
        />
      )}
    </div>
  );

  return (
    <Split
      id="design"
      initial={0.62}
      left={
        <>
          <div className="pane-head">
            <b>Topology</b>
            <span className="faint">drag from a node's edge dot to link · Del removes · click to inspect</span>
          </div>
          {graph}
        </>
      }
      right={
        <>
          <div className="pane-head">
            <b>labs/{name}.yaml</b>
            <div className="grow" />
            {status}
          </div>
          <div style={{ flex: 1, minHeight: 0 }}>
            <CodeEditor value={text} onChange={onChange} diagnostics={diagnostics} completions={completions} onSave={onSave} />
          </div>
        </>
      }
    />
  );
}
