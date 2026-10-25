import { Copy, Network, Play, Plus, Square } from "lucide-react";
import { createLab, labAction } from "../components/Sidebar";
import { useStore } from "../lib/store";

const SNIPPETS: [string, string][] = [
  ["Run one command (and its children) through a 3G link", "netsim run -p 3g-hspa -- curl -sI https://example.com"],
  ["Open a shell where everything goes through a flaky cellular link", "netsim run -p gsm-flaky -- bash"],
  ["Reach a local server/container on :8080 from inside the shaped sandbox", "netsim run -p 4g-lte-poor --forward 8080 -- ./my-client http://localhost:8080"],
  ["Put a shaped link between an unmodified client and server on this machine", "netsim pipe 6000:5000 -p edge   # client connects to :6000 instead of :5000"],
  ["Run a program inside a node of a running lab", "netsim exec iot-gateway sensor1 -- ./my-agent"],
];

export default function Home() {
  const labs = useStore((s) => s.labs);
  const profiles = useStore((s) => s.profiles);
  const openTab = useStore((s) => s.openTab);
  const info = useStore((s) => s.info);
  const running = labs.filter((l) => l.state !== "stopped");

  return (
    <div className="home">
      <h1>Network simulator</h1>
      <p className="muted" style={{ marginTop: 0 }}>
        {labs.length} labs · {profiles.length} profiles · {running.length} running
        {info && <> · files in <code>{info.home}</code> · programs run as <b>{info.user}</b></>}
      </p>

      <div className="home-grid" style={{ marginTop: 16 }}>
        <div className="card">
          <div className="card-head">
            <span className="grow">Labs</span>
            <button className="btn small" onClick={() => createLab()}><Plus size={12} /> New</button>
          </div>
          <div>
            {labs.map((l) => (
              <div key={l.name} className="side-item" onClick={() => openTab("lab", l.name)}>
                <span className={`dot ${l.state}`} />
                <span className="name">
                  <b>{l.name}</b>
                  <div className="faint" style={{ fontSize: 11, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                    {l.description}
                  </div>
                </span>
                <span onClick={(e) => e.stopPropagation()}>
                  {l.state === "running" ? (
                    <button className="btn small" onClick={() => labAction(l.name, "down")}><Square size={12} /> Stop</button>
                  ) : (
                    <button className="btn small" onClick={() => labAction(l.name, "up")}><Play size={12} /> Start</button>
                  )}
                </span>
              </div>
            ))}
            {!labs.length && <div className="card-body faint">No labs yet.</div>}
          </div>
        </div>

        <div className="card">
          <div className="card-head">From the terminal</div>
          <div className="card-body">
            {SNIPPETS.map(([title, cmd]) => (
              <div key={cmd}>
                <div className="muted">{title}</div>
                <div className="row" style={{ alignItems: "stretch" }}>
                  <pre className="snippet mono grow">{cmd}</pre>
                  <button className="icon-btn" style={{ marginTop: 6 }} title="Copy" onClick={() => navigator.clipboard.writeText(cmd)}>
                    <Copy size={14} />
                  </button>
                </div>
              </div>
            ))}
            <div className="faint" style={{ fontSize: 11 }}>
              Every <code>run</code> and <code>pipe</code> is its own temporary lab, visible in the sidebar while it runs - start several for side-by-side comparisons.
            </div>
          </div>
        </div>

        <div className="card">
          <div className="card-head">How it works</div>
          <div className="card-body col" style={{ gap: 6 }}>
            <div><Network size={13} /> Each node is a Linux network namespace; each link a veth pair.</div>
            <div>Each direction of a link is shaped by <code>tc netem</code> at the sending side: delay, jitter, loss, rate, queue, reordering…</div>
            <div>Profiles describe one link's behaviour (and can change over time). Labs wire nodes, links, programs and proxies together.</div>
            <div>The <b>external</b> node is this machine: connect it to give a lab internet access or to reach local services and Docker.</div>
            <div>Everything is plain YAML in <code>profiles/</code> and <code>labs/</code>; the graph editor and the text editor edit the same file.</div>
            <button className="btn small" style={{ alignSelf: "flex-start" }} onClick={() => openTab("help")}>Open the guide</button>
          </div>
        </div>
      </div>
    </div>
  );
}
