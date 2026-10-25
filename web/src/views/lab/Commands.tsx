// SPDX-License-Identifier: LGPL-3.0-or-later
// Copyright (C) 2026 Eduardo Correia
import { Copy } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { api, enc } from "../../lib/api";
import { fmtTime } from "../../lib/format";
import { useStore } from "../../lib/store";
import type { CommandRecord } from "../../lib/types";
import { useTopic } from "../../lib/ws";

/** Every system command netsim ran for this lab - nothing hidden. */
export default function Commands({ name }: { name: string }) {
  const [cmds, setCmds] = useState<CommandRecord[]>([]);
  const [q, setQ] = useState("");
  const [onlyErrors, setOnlyErrors] = useState(false);
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    api.get<CommandRecord[]>(`/api/labs/${enc(name)}/commands`).then(setCmds).catch(() => {});
  }, [name]);
  useTopic(`lab:${name}:cmd`, (c: CommandRecord) => setCmds((xs) => [...xs.slice(-2999), c]));

  const shown = useMemo(
    () => cmds.filter((c) => (!onlyErrors || c.code !== 0) && (!q || c.cmd.includes(q) || (c.input ?? "").includes(q))),
    [cmds, q, onlyErrors],
  );

  const script = () => {
    const lines = ["#!/bin/sh", `# commands netsim ran for lab ${name}`, "set -e"];
    for (const c of shown) {
      if (c.input) lines.push(`${c.cmd} <<'EOF'\n${c.input.trimEnd()}\nEOF`);
      else lines.push(c.cmd);
    }
    navigator.clipboard.writeText(lines.join("\n") + "\n");
    useStore.getState().toast("success", `Copied ${shown.length} commands as a shell script`);
  };

  return (
    <>
      <div className="pane-head">
        <b>System commands</b>
        <span className="faint">ip / tc / nft / iptables run for this lab, in order</span>
        <span className="grow" />
        <input placeholder="filter…" value={q} onChange={(e) => setQ(e.target.value)} style={{ width: 180 }} />
        <label className="row" style={{ gap: 4 }}><input type="checkbox" checked={onlyErrors} onChange={(e) => setOnlyErrors(e.target.checked)} /> failures</label>
        <button className="btn small" onClick={script}><Copy size={12} /> Copy as script</button>
      </div>
      <div className="scroll log" ref={box}>
        {shown.map((c, i) => (
          <div key={i} className="ln">
            <span className="ts">{fmtTime(c.t)}</span>
            <span className={c.code ? "error" : ""} style={{ minWidth: 0 }}>
              {c.cmd}
              {c.input && <div className="faint" style={{ paddingLeft: 16 }}>{c.input.trimEnd()}</div>}
              {c.code !== 0 && <div className="error" style={{ paddingLeft: 16 }}>exit {c.code}: {c.stderr}</div>}
            </span>
          </div>
        ))}
      </div>
    </>
  );
}
