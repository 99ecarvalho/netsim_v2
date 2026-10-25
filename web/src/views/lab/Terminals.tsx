import { FitAddon } from "@xterm/addon-fit";
import { Terminal as XTerm } from "@xterm/xterm";
import { Plus, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Empty } from "../../components/ui";
import { wsUrl } from "../../lib/api";
import type { Runtime } from "../../lib/types";

interface Term {
  id: number;
  node: string;
  root: boolean;
}

let seq = 0;

export default function Terminals({ name, runtime, visible }: { name: string; runtime: Runtime; visible: boolean }) {
  const nodes = Object.values(runtime.plan.nodes).filter((n) => n.kind !== "switch");
  const [terms, setTerms] = useState<Term[]>([]);
  const [active, setActive] = useState<number | null>(null);
  const [node, setNode] = useState(nodes[0]?.name ?? "");
  const [root, setRoot] = useState(false);

  const open = () => {
    const t = { id: ++seq, node, root };
    setTerms((ts) => [...ts, t]);
    setActive(t.id);
  };
  const close = (id: number) => {
    setTerms((ts) => ts.filter((t) => t.id !== id));
    setActive((a) => (a === id ? terms.find((t) => t.id !== id)?.id ?? null : a));
  };

  return (
    <>
      <div className="pane-head">
        {terms.map((t) => (
          <span key={t.id} className={`badge ${t.id === active ? "accent" : ""}`} style={{ cursor: "pointer" }} onClick={() => setActive(t.id)}>
            {t.root ? "#" : "$"} {t.node}
            <X size={11} onClick={(e) => (e.stopPropagation(), close(t.id))} />
          </span>
        ))}
        <span className="grow" />
        <select value={node} onChange={(e) => setNode(e.target.value)}>
          {nodes.map((n) => <option key={n.name}>{n.name}</option>)}
        </select>
        <label className="row" style={{ gap: 4 }}><input type="checkbox" checked={root} onChange={(e) => setRoot(e.target.checked)} /> root</label>
        <button className="btn small primary" onClick={open} disabled={!node}>
          <Plus size={12} /> Shell
        </button>
      </div>
      {terms.length === 0 && (
        <Empty title="Open a shell inside a node">
          Everything you run there uses the node's network: addresses, routes and the shaped links. Same as <code>netsim exec {name} NODE</code>.
        </Empty>
      )}
      {terms.map((t) => (
        <TermView key={t.id} lab={name} term={t} shown={visible && t.id === active} onExit={() => {}} />
      ))}
    </>
  );
}

function TermView({ lab, term, shown }: { lab: string; term: Term; shown: boolean; onExit: () => void }) {
  const ref = useRef<HTMLDivElement>(null);
  const fit = useRef<FitAddon | null>(null);

  useEffect(() => {
    const xt = new XTerm({
      fontFamily: '"JetBrains Mono", ui-monospace, Menlo, monospace',
      fontSize: 13,
      cursorBlink: true,
      theme: { background: "#0d0d10", foreground: "#e6e6ea", cursor: "#8080f2", selectionBackground: "#3a3a70" },
      scrollback: 5000,
    });
    const f = new FitAddon();
    fit.current = f;
    xt.loadAddon(f);
    xt.open(ref.current!);
    f.fit();
    const ws = new WebSocket(
      wsUrl(`/api/term?lab=${encodeURIComponent(lab)}&node=${encodeURIComponent(term.node)}&root=${term.root ? 1 : 0}&cols=${xt.cols}&rows=${xt.rows}`),
    );
    ws.binaryType = "arraybuffer";
    ws.onmessage = (ev) => {
      if (typeof ev.data === "string") {
        const m = JSON.parse(ev.data);
        if (m.type === "exit") xt.write("\r\n\x1b[2m[session ended]\x1b[0m\r\n");
        if (m.type === "error") xt.write(`\r\n\x1b[31m${m.message}\x1b[0m\r\n`);
      } else {
        xt.write(new Uint8Array(ev.data));
      }
    };
    ws.onclose = () => xt.write("\r\n\x1b[2m[disconnected]\x1b[0m\r\n");
    const enc = new TextEncoder();
    const d1 = xt.onData((d) => ws.readyState === WebSocket.OPEN && ws.send(enc.encode(d)));
    const d2 = xt.onResize(({ cols, rows }) => ws.readyState === WebSocket.OPEN && ws.send(JSON.stringify({ type: "resize", cols, rows })));
    const ro = new ResizeObserver(() => {
      if (ref.current?.offsetParent) f.fit();
    });
    ro.observe(ref.current!);
    xt.focus();
    return () => {
      ro.disconnect();
      d1.dispose();
      d2.dispose();
      ws.close();
      xt.dispose();
    };
  }, [lab, term]);

  useEffect(() => {
    if (shown) setTimeout(() => fit.current?.fit(), 0);
  }, [shown]);

  return <div className="term" style={{ display: shown ? "block" : "none" }} ref={ref} />;
}
