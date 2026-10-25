import dagre from "@dagrejs/dagre";
import {
  Background,
  BackgroundVariant,
  BaseEdge,
  ConnectionMode,
  Controls,
  EdgeLabelRenderer,
  Handle,
  Position,
  ReactFlow,
  ReactFlowProvider,
  useConnection,
  useInternalNode,
  useNodesState,
  useReactFlow,
  type Edge,
  type EdgeProps,
  type InternalNode,
  type Node,
  type NodeProps,
} from "@xyflow/react";
import { Container, Globe, LayoutGrid, Monitor, Network, Router } from "lucide-react";
import { memo, useCallback, useEffect, useMemo } from "react";
import { fmtBps } from "../lib/format";
import type { LabSpec, NodeKind, Plan, ProgramRuntime, Runtime, StatsSample } from "../lib/types";
import { nodeIcon } from "./icons";

export type Selection = { type: "node"; id: string } | { type: "link"; id: string; index: number } | null;

interface NetNodeData extends Record<string, unknown> {
  name: string;
  kind: NodeKind;
  icon?: string;
  ips: string[];
  live: boolean;
  programs: ProgramRuntime[];
  editable: boolean;
  internet?: boolean;
}

interface NetEdgeData extends Record<string, unknown> {
  index: number;
  label: string;
  sub?: string;
  live: boolean;
  upBps: number;
  downBps: number;
  drops: number;
  cut: boolean;
  step?: string | null;
  title?: string;
}

const NODE_W = 172;
const NODE_H = 58;

export function autoLayout(data: LabSpec): Record<string, { x: number; y: number }> {
  const g = new dagre.graphlib.Graph();
  g.setGraph({ rankdir: "LR", nodesep: 50, ranksep: 140, marginx: 20, marginy: 20 });
  g.setDefaultEdgeLabel(() => ({}));
  for (const name of Object.keys(data.nodes ?? {})) g.setNode(name, { width: NODE_W, height: NODE_H });
  for (const l of data.links ?? []) if (data.nodes?.[l.a] !== undefined && data.nodes?.[l.b] !== undefined) g.setEdge(l.a, l.b);
  dagre.layout(g);
  const out: Record<string, { x: number; y: number }> = {};
  for (const name of g.nodes()) {
    const n = g.node(name);
    out[name] = { x: n.x - NODE_W / 2, y: n.y - NODE_H / 2 };
  }
  return out;
}

export function linkId(l: { a: string; b: string; name?: string }) {
  return l.name || `${l.a}--${l.b}`;
}

// ------------------------------------------------------------------- node

const NetNode = memo(function NetNode({ id, data, selected }: NodeProps<Node<NetNodeData>>) {
  const connection = useConnection();
  const isTarget = connection.inProgress && connection.fromNode?.id !== id;
  const Icon = nodeIcon(data.kind, data.icon);
  return (
    <div className={`netnode k-${data.kind} ${selected ? "selected" : ""} ${data.live ? "live" : ""} ${isTarget ? "target-hint" : ""}`}>
      <div className="ico">
        <Icon size={18} />
      </div>
      <div style={{ minWidth: 0 }}>
        <div className="title">{data.name}</div>
        {data.ips.length ? (
          data.ips.slice(0, 3).map((ip) => (
            <div className="sub" key={ip}>
              {ip}
            </div>
          ))
        ) : (
          <div className="sub">{data.kind === "switch" ? "L2 bridge" : data.kind === "external" ? (data.internet ? "this host + internet" : "this host") : data.kind === "container" ? "docker container" : data.kind}</div>
        )}
      </div>
      {data.programs.length > 0 && (
        <div className="progs">
          {data.programs.map((p) => (
            <span key={p.name} className={`prog ${p.status}`} title={`${p.name}: ${p.status}`}>
              {p.name[0].toUpperCase()}
            </span>
          ))}
        </div>
      )}
      {/* edges need handles even when read-only; they are hidden and inert then */}
      <Handle type="source" position={Position.Right} className="handle source" isConnectableEnd={false}
        isConnectable={data.editable} style={data.editable ? undefined : { visibility: "hidden" }} />
      <Handle
        type="target"
        position={Position.Left}
        className="handle"
        isConnectableStart={false}
        isConnectable={data.editable}
        style={{ pointerEvents: isTarget ? "all" : "none" }}
      />
    </div>
  );
});

// ------------------------------------------------------------------- edge

function intersection(node: InternalNode, other: InternalNode) {
  const w = (node.measured.width ?? NODE_W) / 2;
  const h = (node.measured.height ?? NODE_H) / 2;
  const p = node.internals.positionAbsolute;
  const o = other.internals.positionAbsolute;
  const x2 = p.x + w;
  const y2 = p.y + h;
  const x1 = o.x + (other.measured.width ?? NODE_W) / 2;
  const y1 = o.y + (other.measured.height ?? NODE_H) / 2;
  const xx1 = (x1 - x2) / (2 * w) - (y1 - y2) / (2 * h);
  const yy1 = (x1 - x2) / (2 * w) + (y1 - y2) / (2 * h);
  const a = 1 / (Math.abs(xx1) + Math.abs(yy1) || 1);
  const xx3 = a * xx1;
  const yy3 = a * yy1;
  return { x: w * (xx3 + yy3) + x2, y: h * (-xx3 + yy3) + y2 };
}

function width(bps: number) {
  if (bps <= 0) return 1.5;
  return Math.min(9, 1.5 + Math.log10(1 + bps / 1000) * 1.6);
}

const NetEdge = memo(function NetEdge({ id, source, target, data, selected }: EdgeProps<Edge<NetEdgeData>>) {
  const s = useInternalNode(source);
  const t = useInternalNode(target);
  if (!s || !t || !data) return null;
  const p1 = intersection(s, t);
  const p2 = intersection(t, s);
  const mx = (p1.x + p2.x) / 2;
  const my = (p1.y + p2.y) / 2;
  const dx = p2.x - p1.x;
  const dy = p2.y - p1.y;
  const len = Math.hypot(dx, dy) || 1;
  const nx = (-dy / len) * 3.5;
  const ny = (dx / len) * 3.5;
  const base = selected ? "var(--accent)" : "var(--border-strong)";

  const labelClass = `edge-label nodrag nopan ${selected ? "selected" : ""} ${data.cut ? "cut" : ""}`;
  const label = (
    <EdgeLabelRenderer>
      <div className={labelClass} title={data.title} style={{ transform: `translate(-50%, -50%) translate(${mx}px, ${my}px)` }}>
        {data.live ? (
          <>
            {data.cut ? (
              <b>✂ cut</b>
            ) : (
              <>
                <span className="up">↑{fmtBps(data.upBps, 2)}</span> <span className="down">↓{fmtBps(data.downBps, 2)}</span>
                {data.drops > 0 && <span className="err"> ✕{data.drops.toFixed(0)}/s</span>}
              </>
            )}
            <div className="faint" style={{ fontSize: 10 }}>{data.label}</div>
            {data.step && <div className="step">{data.step}</div>}
          </>
        ) : (
          <>
            <div>{data.label}</div>
            {data.sub && <div className="faint" style={{ fontSize: 10 }}>{data.sub}</div>}
          </>
        )}
      </div>
    </EdgeLabelRenderer>
  );

  if (!data.live) {
    return (
      <>
        <BaseEdge id={id} path={`M${p1.x},${p1.y} L${p2.x},${p2.y}`} style={{ stroke: base, strokeWidth: selected ? 2.5 : 1.8 }} interactionWidth={18} />
        {label}
      </>
    );
  }
  const upPath = `M${p1.x + nx},${p1.y + ny} L${p2.x + nx},${p2.y + ny}`;
  const downPath = `M${p2.x - nx},${p2.y - ny} L${p1.x - nx},${p1.y - ny}`;
  const anim = (bps: number) =>
    bps > 0 ? { strokeDasharray: "7 5", animation: `flowdash ${Math.max(0.25, 1.4 - Math.log10(1 + bps / 1000) * 0.25)}s linear infinite` } : {};
  return (
    <>
      <BaseEdge id={id} path={`M${p1.x},${p1.y} L${p2.x},${p2.y}`} style={{ stroke: "transparent" }} interactionWidth={22} />
      {data.cut ? (
        <path d={`M${p1.x},${p1.y} L${p2.x},${p2.y}`} style={{ stroke: "var(--danger)", strokeWidth: 2, strokeDasharray: "4 4", fill: "none" }} />
      ) : (
        <>
          <path d={upPath} style={{ stroke: data.upBps > 0 ? "var(--up)" : base, strokeWidth: width(data.upBps), fill: "none", opacity: 0.9, ...anim(data.upBps) }} />
          <path d={downPath} style={{ stroke: data.downBps > 0 ? "var(--down)" : base, strokeWidth: width(data.downBps), fill: "none", opacity: 0.9, ...anim(data.downBps) }} />
        </>
      )}
      {label}
    </>
  );
});

const nodeTypes = { net: NetNode };
const edgeTypes = { net: NetEdge };

// ------------------------------------------------------------------- graph

interface Props {
  data: LabSpec;
  plan?: Plan | null;
  runtime?: Runtime | null;
  stats?: StatsSample | null;
  editable: boolean;
  selection: Selection;
  onSelect: (s: Selection) => void;
  onMove?: (positions: Record<string, { x: number; y: number }>) => void;
  onConnect?: (a: string, b: string) => void;
  onDelete?: (nodes: string[], linkIndexes: number[]) => void;
  onAdd?: (kind: NodeKind, pos: { x: number; y: number }) => void;
}

function Graph(props: Props) {
  const { data, plan, runtime, stats, editable, selection, onSelect } = props;
  const rf = useReactFlow();
  const live = !!runtime;

  const positions = useMemo(() => {
    const layout = data.layout ?? {};
    const names = Object.keys(data.nodes ?? {});
    const missing = names.some((n) => !layout[n]);
    const auto = missing ? autoLayout(data) : {};
    return Object.fromEntries(names.map((n) => [n, layout[n] ?? auto[n] ?? { x: 0, y: 0 }]));
  }, [data]);

  const built: Node<NetNodeData>[] = useMemo(
    () =>
      Object.entries(data.nodes ?? {}).map(([name, spec]) => {
        const pn = plan?.nodes[name];
        return {
          id: name,
          type: "net",
          position: positions[name],
          selected: selection?.type === "node" && selection.id === name,
          data: {
            name,
            kind: (spec?.kind ?? "host") as NodeKind,
            icon: spec?.icon,
            internet: spec?.internet,
            ips: pn ? pn.ifaces.filter((i) => i.ip).map((i) => i.ip!) : [],
            live,
            editable,
            programs: Object.values(runtime?.programs ?? {}).filter((p) => p.node === name),
          },
        };
      }),
    [data, plan, positions, live, editable, runtime, selection],
  );

  const [nodes, setNodes, onNodesChange] = useNodesState<Node<NetNodeData>>(built);
  useEffect(() => setNodes(built), [built, setNodes]);

  const edges: Edge<NetEdgeData>[] = useMemo(
    () =>
      (data.links ?? [])
        .map((l, index) => {
          const id = linkId(l);
          const lr = runtime?.links[id];
          const st = stats?.links[id];
          const profile = (live ? lr?.profile : l.profile) ?? "no profile";
          const overrides = Object.keys(l).filter((k) => !["a", "b", "name", "profile", "subnet", "ip_a", "ip_b", "mtu"].includes(k));
          const cut = !!(lr?.up?.summary === "blackhole" && lr?.down?.summary === "blackhole");
          return {
            id,
            source: l.a,
            target: l.b,
            type: "net",
            selected: selection?.type === "link" && selection.index === index,
            data: {
              index,
              label: l.name ? `${l.name}: ${profile}` : profile,
              sub: overrides.length ? `+ ${overrides.join(", ")}` : undefined,
              title: plan?.links[id]?.shaping ? `a→b: ${plan.links[id].shaping!.up}\nb→a: ${plan.links[id].shaping!.down}` : undefined,
              live,
              upBps: st?.up?.bps ?? 0,
              downBps: st?.down?.bps ?? 0,
              drops: (st?.up?.dps ?? 0) + (st?.down?.dps ?? 0),
              cut,
              step: lr?.step_label,
            },
          };
        })
        .filter((e) => data.nodes?.[e.source] !== undefined && data.nodes?.[e.target] !== undefined),
    [data, runtime, stats, live, selection, plan],
  );

  const addAtCenter = useCallback(
    (kind: NodeKind) => {
      const el = document.querySelector(".react-flow") as HTMLElement | null;
      const rect = el?.getBoundingClientRect();
      const center = rect ? { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 } : { x: 300, y: 200 };
      const p = rf.screenToFlowPosition(center);
      props.onAdd?.(kind, { x: p.x - NODE_W / 2 + Math.random() * 40, y: p.y - NODE_H / 2 + Math.random() * 40 });
    },
    [rf, props],
  );

  return (
    <ReactFlow
      nodes={nodes}
      edges={edges}
      nodeTypes={nodeTypes}
      edgeTypes={edgeTypes}
      onNodesChange={onNodesChange}
      connectionMode={ConnectionMode.Loose}
      nodesDraggable
      nodesConnectable={editable}
      elementsSelectable
      deleteKeyCode={editable ? ["Delete", "Backspace"] : null}
      fitView
      fitViewOptions={{ padding: 0.25, maxZoom: 1.2 }}
      minZoom={0.2}
      onNodeClick={(_, n) => onSelect({ type: "node", id: n.id })}
      onEdgeClick={(_, e) => onSelect({ type: "link", id: e.id, index: (e.data as NetEdgeData).index })}
      onPaneClick={() => onSelect(null)}
      onNodeDragStop={(_, __, dragged) => {
        const pos = Object.fromEntries(dragged.map((n) => [n.id, n.position]));
        if (editable) props.onMove?.(pos);
      }}
      onConnect={(c) => c.source && c.target && c.source !== c.target && props.onConnect?.(c.source, c.target)}
      onDelete={({ nodes: ns, edges: es }) =>
        props.onDelete?.(ns.map((n) => n.id), es.map((e) => (e.data as NetEdgeData).index))
      }
      proOptions={{ hideAttribution: true }}
    >
      <Background variant={BackgroundVariant.Dots} gap={18} size={1.4} color="var(--dot)" />
      <Controls showInteractive={false} position="bottom-right" />
      {editable && (
        <div className="palette">
          <button className="btn small ghost" onClick={() => addAtCenter("host")} title="Add a host (namespace)">
            <Monitor size={14} /> Host
          </button>
          <button className="btn small ghost" onClick={() => addAtCenter("router")} title="Add a router">
            <Router size={14} /> Router
          </button>
          <button className="btn small ghost" onClick={() => addAtCenter("switch")} title="Add a switch (L2 segment)">
            <Network size={14} /> Switch
          </button>
          <button className="btn small ghost" onClick={() => addAtCenter("external")} title="The real host / internet (max one)"
            disabled={Object.values(data.nodes ?? {}).some((n) => n?.kind === "external")}>
            <Globe size={14} /> External
          </button>
          <button className="btn small ghost" onClick={() => addAtCenter("container")} title="Attach a running Docker container">
            <Container size={14} /> Container
          </button>
          <span style={{ width: 1, background: "var(--border)", margin: "2px 2px" }} />
          <button className="btn small ghost" title="Arrange automatically" onClick={() => {
            props.onMove?.(autoLayout(data));
            setTimeout(() => rf.fitView({ padding: 0.25, duration: 300, maxZoom: 1.2 }), 50);
          }}>
            <LayoutGrid size={14} />
          </button>
        </div>
      )}
      {live && (
        <div className="legend">
          <span><span className="up">━</span> a→b (up)</span>
          <span><span className="down">━</span> b→a (down)</span>
          <span>width ~ throughput</span>
        </div>
      )}
    </ReactFlow>
  );
}

export default function Topology(props: Props) {
  return (
    <ReactFlowProvider>
      <style>{`@keyframes flowdash { to { stroke-dashoffset: -24; } }`}</style>
      <Graph {...props} />
    </ReactFlowProvider>
  );
}
