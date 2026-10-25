// SPDX-License-Identifier: LGPL-3.0-or-later
// Copyright (C) 2026 Eduardo Correia
import { ArrowDownLeft, ArrowUpRight, Download, Pause, Play, Square, Trash2 } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import Chart, { type SeriesDef } from "../../components/Chart";
import { Empty, Split } from "../../components/ui";
import { api, enc } from "../../lib/api";
import { fmtBps, fmtTime } from "../../lib/format";
import { toastError } from "../../lib/store";
import type { CaptureInfo, Packet, ProbeInfo, ProbeResult, Runtime } from "../../lib/types";
import { useTopic } from "../../lib/ws";

const MAX_PACKETS = 5000;

export default function Traffic({ name, runtime }: { name: string; runtime: Runtime }) {
  return (
    <Split
      id="traffic"
      initial={0.6}
      left={<Captures name={name} runtime={runtime} />}
      right={<Probes name={name} runtime={runtime} />}
    />
  );
}

// ------------------------------------------------------------------ captures

function Captures({ name, runtime }: { name: string; runtime: Runtime }) {
  const links = Object.values(runtime.plan.links);
  const [link, setLink] = useState(links[0]?.id ?? "");
  const [side, setSide] = useState<"a" | "b">("a");
  const [filter, setFilter] = useState("");
  const [selected, setSelected] = useState<string | null>(runtime.captures.find((c) => c.running)?.id ?? null);
  const pl = runtime.plan.links[link];

  const start = async () => {
    try {
      const cap = await api.post<CaptureInfo>(`/api/labs/${enc(name)}/captures`, { link, side, filter });
      setSelected(cap.id);
    } catch (e) {
      toastError(e);
    }
  };

  const current = runtime.captures.find((c) => c.id === selected) ?? null;

  return (
    <>
      <div className="pane-head" style={{ flexWrap: "wrap" }}>
        <b>Capture</b>
        <select value={link} onChange={(e) => setLink(e.target.value)}>
          {links.map((l) => (
            <option key={l.id} value={l.id}>{l.id}</option>
          ))}
        </select>
        {pl && (
          <select value={side} onChange={(e) => setSide(e.target.value as "a" | "b")} title="Which end of the link to listen on">
            <option value="a">at {pl.a.node} ({pl.a.name})</option>
            <option value="b">at {pl.b.node} ({pl.b.name})</option>
          </select>
        )}
        <input className="mono grow" style={{ minWidth: 140 }} placeholder="display filter, e.g. tcp port 8000" value={filter}
          onChange={(e) => setFilter(e.target.value)} onKeyDown={(e) => e.key === "Enter" && start()} />
        <button className="btn primary small" onClick={start} disabled={!link}>
          <Play size={12} /> Start
        </button>
      </div>
      {runtime.captures.length > 0 && (
        <div className="pane-head" style={{ gap: 6, flexWrap: "wrap" }}>
          {runtime.captures.map((c) => (
            <span key={c.id} className={`badge ${c.id === selected ? "accent" : ""}`} style={{ cursor: "pointer" }} onClick={() => setSelected(c.id)}>
              {c.running && <span className="dot running" />}
              {c.link} @{c.node}{c.filter && ` · ${c.filter}`}
            </span>
          ))}
        </div>
      )}
      {current ? (
        <PacketTable key={current.id} cap={current} onDelete={() => setSelected(null)} />
      ) : (
        <Empty title="No capture selected">
          Pick a link and start a capture. Packets are shown live and saved as a .pcap you can open in Wireshark.
        </Empty>
      )}
    </>
  );
}

function PacketTable({ cap, onDelete }: { cap: CaptureInfo; onDelete: () => void }) {
  const [packets, setPackets] = useState<Packet[]>([]);
  const [paused, setPaused] = useState(false);
  const [q, setQ] = useState("");
  const [dropped, setDropped] = useState(0);
  const pausedRef = useRef(paused);
  pausedRef.current = paused;
  const box = useRef<HTMLDivElement>(null);
  const stick = useRef(true);

  useTopic(cap.running ? `capture:${cap.id}` : null, (d: { packets: Packet[]; dropped: number }) => {
    if (pausedRef.current) return;
    if (d.dropped) setDropped((x) => x + d.dropped);
    setPackets((xs) => {
      const next = xs.concat(d.packets);
      return next.length > MAX_PACKETS ? next.slice(next.length - MAX_PACKETS) : next;
    });
  });

  useEffect(() => {
    if (stick.current && box.current) box.current.scrollTop = box.current.scrollHeight;
  }, [packets]);

  const shown = useMemo(() => {
    const needle = q.toLowerCase();
    return needle ? packets.filter((p) => `${p.proto} ${p.src}:${p.sport} ${p.dst}:${p.dport} ${p.info}`.toLowerCase().includes(needle)) : packets;
  }, [packets, q]);

  const t0 = packets[0]?.t ?? 0;
  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const p of packets) c[p.proto] = (c[p.proto] ?? 0) + 1;
    return c;
  }, [packets]);

  const stop = () => api.post(`/api/captures/${cap.id}/stop`).catch(toastError);
  const del = async () => {
    await api.del(`/api/captures/${cap.id}`).catch(toastError);
    onDelete();
  };

  return (
    <>
      <div className="pane-head">
        {cap.running ? (
          <>
            <button className="btn small" onClick={() => setPaused(!paused)}>
              {paused ? <Play size={12} /> : <Pause size={12} />} {paused ? "Resume" : "Pause"}
            </button>
            <button className="btn small danger" onClick={stop}>
              <Square size={12} /> Stop
            </button>
          </>
        ) : (
          <span className={`badge ${cap.truncated ? "warn" : ""}`} title={cap.truncated ? "Captures stop at 200 MiB" : ""}>
            {cap.truncated ? "stopped: size limit" : "stopped"}
          </span>
        )}
        <a className="btn small" href={`/api/captures/${cap.id}/pcap`} download>
          <Download size={12} /> .pcap
        </a>
        <button className="icon-btn sm" title="Delete capture and file" onClick={del}>
          <Trash2 size={13} />
        </button>
        <input placeholder="search…" value={q} onChange={(e) => setQ(e.target.value)} style={{ width: 160 }} />
        <span className="grow" />
        {Object.entries(counts).map(([p, n]) => (
          <span key={p} className={`proto ${["tcp", "udp", "icmp", "arp"].includes(p) ? p : "other"}`}>{p} {n}</span>
        ))}
        <span className="faint">{packets.length} shown{dropped ? ` · ${dropped} skipped (too fast)` : ""}</span>
      </div>
      <div className="scroll" ref={box} onScroll={(e) => {
        const el = e.currentTarget;
        stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
      }}>
        <table className="grid compact pkt">
          <thead>
            <tr>
              <th>time</th>
              <th></th>
              <th>proto</th>
              <th>source</th>
              <th>destination</th>
              <th>len</th>
              <th>info</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((p, i) => (
              <tr key={i}>
                <td className="faint">{(p.t - t0).toFixed(3)}</td>
                <td title={p.dir === "out" ? `sent by ${cap.node}` : `received by ${cap.node}`}>
                  {p.dir === "out" ? <ArrowUpRight size={12} className="up" /> : p.dir === "in" ? <ArrowDownLeft size={12} className="down" /> : null}
                </td>
                <td><span className={`proto ${["tcp", "udp", "icmp", "arp"].includes(p.proto) ? p.proto : "other"}`}>{p.proto}</span></td>
                <td>{p.src}{p.sport != null && <span className="faint">:{p.sport}</span>}</td>
                <td>{p.dst}{p.dport != null && <span className="faint">:{p.dport}</span>}</td>
                <td>{p.len ?? ""}</td>
                <td style={{ whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", maxWidth: 520 }} title={p.info}>
                  {p.flags && <b>[{p.flags}] </b>}
                  {p.info.replace(/^Flags \[[^\]]*\],?\s*/, "")}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {!packets.length && <div className="empty">{cap.running ? "Waiting for packets…" : "No packets received in this view. Download the .pcap for the full capture."}</div>}
      </div>
    </>
  );
}

// ------------------------------------------------------------------ probes

const RTT: SeriesDef[] = [{ label: "rtt", color: "--accent", points: true, width: 1.2 }];
const IPERF: SeriesDef[] = [{ label: "throughput", color: "--up", fill: true }];

function Probes({ name, runtime }: { name: string; runtime: Runtime }) {
  const nodes = Object.values(runtime.plan.nodes).filter((n) => n.kind !== "switch").map((n) => n.name);
  const [kind, setKind] = useState<"ping" | "iperf">("ping");
  const [src, setSrc] = useState(nodes[0] ?? "");
  const [dst, setDst] = useState(nodes[1] ?? nodes[0] ?? "");
  const [opts, setOpts] = useState({ interval: "0.2", duration: "10", reverse: false, udp: false, bitrate: "1M", parallel: "1" });
  const [selected, setSelected] = useState<string | null>(runtime.probes[runtime.probes.length - 1]?.id ?? null);

  const start = async () => {
    const options = kind === "ping"
      ? { interval: Number(opts.interval) }
      : { duration: Number(opts.duration), reverse: opts.reverse, udp: opts.udp, bitrate: opts.bitrate, parallel: Number(opts.parallel) };
    try {
      const p = await api.post<ProbeInfo>(`/api/labs/${enc(name)}/probes`, { kind, src, dst, options });
      setSelected(p.id);
    } catch (e) {
      toastError(e);
    }
  };

  const current = runtime.probes.find((p) => p.id === selected) ?? null;

  return (
    <>
      <div className="pane-head" style={{ flexWrap: "wrap" }}>
        <b>Probe</b>
        <div className="seg">
          <button className={kind === "ping" ? "on" : ""} onClick={() => setKind("ping")}>ping</button>
          <button className={kind === "iperf" ? "on" : ""} onClick={() => setKind("iperf")}>iperf3</button>
        </div>
        <select value={src} onChange={(e) => setSrc(e.target.value)}>{nodes.map((n) => <option key={n}>{n}</option>)}</select>→
        <select value={dst} onChange={(e) => setDst(e.target.value)}>{nodes.map((n) => <option key={n}>{n}</option>)}</select>
        {kind === "ping" ? (
          <label className="row" style={{ gap: 4 }}>every <input className="mono" style={{ width: 50 }} value={opts.interval} onChange={(e) => setOpts({ ...opts, interval: e.target.value })} />s</label>
        ) : (
          <>
            <input className="mono" style={{ width: 44 }} value={opts.duration} title="seconds" onChange={(e) => setOpts({ ...opts, duration: e.target.value })} />s
            <label className="row" style={{ gap: 3 }} title="server sends (b→a direction)"><input type="checkbox" checked={opts.reverse} onChange={(e) => setOpts({ ...opts, reverse: e.target.checked })} />reverse</label>
            <label className="row" style={{ gap: 3 }}><input type="checkbox" checked={opts.udp} onChange={(e) => setOpts({ ...opts, udp: e.target.checked })} />udp</label>
            {opts.udp && <input className="mono" style={{ width: 54 }} value={opts.bitrate} title="UDP bitrate" onChange={(e) => setOpts({ ...opts, bitrate: e.target.value })} />}
          </>
        )}
        <button className="btn primary small" onClick={start} disabled={!src || !dst || src === dst}>
          <Play size={12} /> Run
        </button>
      </div>
      {runtime.probes.length > 0 && (
        <div className="pane-head" style={{ gap: 6, flexWrap: "wrap" }}>
          {runtime.probes.slice(-8).map((p) => (
            <span key={p.id} className={`badge ${p.id === selected ? "accent" : ""}`} style={{ cursor: "pointer" }} onClick={() => setSelected(p.id)}>
              {p.running && <span className="dot running" />}
              {p.kind} {p.src}→{p.dst}
            </span>
          ))}
        </div>
      )}
      <div className="scroll">
        {current ? <ProbeView key={current.id} probe={current} /> : (
          <Empty title="No probe yet">ping measures RTT and loss between two nodes; iperf3 measures achievable throughput over the shaped path.</Empty>
        )}
      </div>
    </>
  );
}

function ProbeView({ probe }: { probe: ProbeInfo }) {
  const [results, setResults] = useState<ProbeResult[]>([]);
  useEffect(() => {
    api.get<ProbeInfo>(`/api/probes/${probe.id}`).then((p) => setResults(p.results ?? [])).catch(() => {});
  }, [probe.id]);
  useTopic(`probe:${probe.id}`, (r: ProbeResult) => setResults((xs) => [...xs.slice(-2999), r]));

  const stop = () => api.post(`/api/probes/${probe.id}/stop`).catch(toastError);

  if (probe.kind === "ping") {
    const pts = results.filter((r) => r.seq != null);
    const rtts = pts.map((r) => r.rtt).filter((v): v is number => v != null);
    const lost = pts.length - rtts.length;
    const sorted = [...rtts].sort((a, b) => a - b);
    const pct = (p: number) => (sorted.length ? sorted[Math.min(sorted.length - 1, Math.floor(p * sorted.length))] : 0);
    const avg = rtts.reduce((a, b) => a + b, 0) / (rtts.length || 1);
    const jitter = rtts.length > 1 ? rtts.slice(1).reduce((a, v, i) => a + Math.abs(v - rtts[i]), 0) / (rtts.length - 1) : 0;
    const data = [pts.map((r) => r.t), pts.map((r) => r.rtt ?? null)];
    return (
      <div className="col" style={{ padding: 12 }}>
        <div className="row">
          <b>ping {probe.src} → {probe.dst}</b>
          <span className="grow" />
          {probe.running && <button className="btn small danger" onClick={stop}><Square size={12} /> Stop</button>}
        </div>
        <div className="stats">
          <Stat v={`${avg.toFixed(1)}`} l="avg rtt ms" />
          <Stat v={`${pct(0.5).toFixed(1)}`} l="p50 ms" />
          <Stat v={`${pct(0.95).toFixed(1)}`} l="p95 ms" />
          <Stat v={`${(sorted[sorted.length - 1] ?? 0).toFixed(1)}`} l="max ms" />
          <Stat v={`${jitter.toFixed(1)}`} l="jitter ms" />
          <Stat v={`${pts.length ? ((lost / pts.length) * 100).toFixed(1) : "0"}%`} l={`loss (${lost}/${pts.length})`} cls={lost ? "err" : ""} />
        </div>
        <Chart series={RTT} data={data} height={220} yFormat={(v) => `${v.toFixed(0)}ms`} window={60} />
        <span className="faint" style={{ fontSize: 11 }}>Gaps are lost packets. RTT includes both directions of every link on the path.</span>
      </div>
    );
  }

  const intervals = results.filter((r) => r.bps != null && !r.summary);
  const summary = results.filter((r) => r.summary);
  const errors = results.filter((r) => r.error);
  return (
    <div className="col" style={{ padding: 12 }}>
      <div className="row">
        <b>iperf3 {probe.src} → {probe.dst}{probe.options.reverse ? " (reverse)" : ""}{probe.options.udp ? " udp" : ""}</b>
        <span className="grow" />
        {probe.running ? <button className="btn small danger" onClick={stop}><Square size={12} /> Stop</button> : <span className="badge">done</span>}
      </div>
      {errors.map((e, i) => <div key={i} className="banner error">{e.error}</div>)}
      <div className="stats">
        {summary.map((s, i) => (
          <Stat key={i} v={fmtBps(s.bps)} l={`${s.role ?? "summary"} bit/s${s.retr != null ? ` · ${s.retr} retr` : ""}${s.loss_pct != null ? ` · ${s.loss_pct}% loss` : ""}`} />
        ))}
        {!summary.length && intervals.length > 0 && <Stat v={fmtBps(intervals[intervals.length - 1].bps)} l="current bit/s" />}
      </div>
      <Chart series={IPERF} data={[intervals.map((r) => r.t), intervals.map((r) => r.bps ?? null)]} height={200} yFormat={(v) => fmtBps(v, 2)} />
      <table className="grid compact">
        <tbody>
          {intervals.map((r, i) => (
            <tr key={i}>
              <td className="mono faint">{fmtTime(r.t)}</td>
              <td className="mono">{r.start?.toFixed(0)}-{r.end?.toFixed(0)}s</td>
              <td className="mono">{fmtBps(r.bps)}bit/s</td>
              <td className="mono faint">{r.retr != null ? `${r.retr} retr` : r.loss_pct != null ? `${r.loss_pct}% loss` : ""}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Stat({ v, l, cls = "" }: { v: string; l: string; cls?: string }) {
  return (
    <div className="stat">
      <span className={`v ${cls}`}>{v}</span>
      <span className="l">{l}</span>
    </div>
  );
}
