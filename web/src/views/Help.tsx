const PROFILE_FIELDS: [string, string, string][] = [
  ["delay", "40ms", "mean one-way delay; RTT over a link is roughly 2×"],
  ["jitter", "10ms", "delay variation: ±range for uniform, std-dev for other distributions"],
  ["distribution", "normal", "uniform, normal, pareto, paretonormal, exponential, lognormal, weibull"],
  ["delay_correlation", "25%", "how much each delay depends on the previous one"],
  ["preserve_order", "true", "keep FIFO order despite jitter (default true)"],
  ["rate", "5mbit", "bandwidth in bits/s (kbit, mbit, gbit; mbps means megabits)"],
  ["queue", "200ms | 100", "bottleneck buffer, time at link rate or packets; large = bufferbloat"],
  ["loss", "1%", "random loss (+ loss_correlation)"],
  ["burst_loss", "{p: 1%, r: 25%}", "Gilbert-Elliott bursty loss: p good→bad, r bad→good, bad_loss, good_loss"],
  ["corrupt", "0.1%", "flip a random bit (+ corrupt_correlation)"],
  ["duplicate", "1%", "send twice (+ duplicate_correlation)"],
  ["reorder", "5%", "send immediately, overtaking delayed packets (needs delay; + reorder_correlation, reorder_gap)"],
  ["slot", "{min: 2ms, max: 8ms}", "release packets in bursts, like Wi-Fi/LTE scheduling (packets, bytes limits)"],
  ["ecn", "true", "mark ECN-capable packets instead of dropping them"],
  ["blackhole", "true", "drop everything"],
  ["up / down", "{rate: 1mbit}", "overrides for a→b (up) and b→a (down)"],
  ["extends", "4g-lte", "inherit another profile"],
  ["timeline", "{loop: true, steps: [...]}", "time-varying: each step has duration, label and any field above"],
];

const LAB_FIELDS: [string, string][] = [
  ["nodes.NAME.kind", "host (default), router, switch, external (this machine, max one), container (attach a running Docker container)"],
  ["nodes.NAME.internet", "external only: NAT lab traffic to the internet"],
  ["nodes.NAME.container", "container only: docker name or id"],
  ["nodes.NAME.subnet", "switch only: CIDR for its L2 segment"],
  ["links[].a / b", "the two ends; up = a→b, down = b→a (put the device/client as a)"],
  ["links[].profile", "profile name; any profile field on the link overrides it"],
  ["links[].subnet, ip_a, ip_b, mtu, name", "addressing and naming; all optional"],
  ["programs[]", "name, node, cmd, autostart, restart (no|on-failure|always), user, cwd, env, delay"],
  ["proxies[]", "name, proto, listen, listen_node, target, target_node, via: [entry, exit]"],
  ["subnet", "base CIDR for automatic addresses (default: a free 10.N.0.0/16)"],
  ["layout", "node positions, written by the graph editor"],
];

export default function Help() {
  return (
    <div className="help">
      <h1 style={{ fontSize: 20, marginTop: 0 }}>Guide</h1>

      <h2>Concepts</h2>
      <p>
        A <b>lab</b> is a graph of nodes and links. Every node except <i>external</i> and <i>container</i> is a fresh Linux network
        namespace; links are veth pairs. Addresses and routes are computed for you and every node can reach every other by name
        (<code>ping server</code>). Each direction of each link is shaped independently by netem on the sending interface.
      </p>
      <p>
        A <b>profile</b> is a reusable description of one link's behaviour, stored as <code>profiles/NAME.yaml</code>. Delays are
        one-way. Profiles can inherit (<code>extends</code>), be asymmetric (<code>up</code>/<code>down</code>) and change over time
        (<code>timeline</code>).
      </p>

      <h2>Getting a program's traffic through the simulator</h2>
      <table>
        <tbody>
          <tr><td><code>netsim run -p PROFILE -- CMD</code></td><td>Temporary sandbox: CMD and all its children use a shaped link to this machine (internet, local services, Docker). Add <code>--forward PORT</code> so <code>localhost:PORT</code> inside the sandbox reaches the host's port through the link.</td></tr>
          <tr><td><code>netsim pipe LPORT:TPORT -p PROFILE</code></td><td>Unmodified client and server: the client connects to LPORT, the traffic crosses a shaped link and reaches TPORT. Nothing else is affected.</td></tr>
          <tr><td><code>netsim exec LAB NODE -- CMD</code></td><td>Run CMD inside a node of a running lab, as your user.</td></tr>
          <tr><td><code>programs:</code> in the lab</td><td>netsim starts and supervises the program inside the node and shows its output.</td></tr>
          <tr><td><code>kind: container</code></td><td>Plug a running Docker container into the lab: clients in other nodes reach it through shaped links.</td></tr>
        </tbody>
      </table>
      <p className="muted">
        A process that is already running cannot be moved into another network namespace (kernel rule). Restart it under
        <code> netsim run</code>/<code>exec</code>, or put a <code>pipe</code>/proxy in front of it.
      </p>

      <h2>Profile fields</h2>
      <table>
        <thead><tr><th>field</th><th>example</th><th>meaning</th></tr></thead>
        <tbody>
          {PROFILE_FIELDS.map(([f, e, m]) => (
            <tr key={f}><td><code>{f}</code></td><td><code>{e}</code></td><td>{m}</td></tr>
          ))}
        </tbody>
      </table>

      <h2>Lab fields</h2>
      <table>
        <tbody>
          {LAB_FIELDS.map(([f, m]) => (
            <tr key={f}><td><code>{f}</code></td><td>{m}</td></tr>
          ))}
        </tbody>
      </table>

      <h2>Reading the monitor</h2>
      <p>
        Each link is drawn as two lanes: <span className="up">blue a→b</span> and <span className="down">amber b→a</span>; width grows
        with throughput. Numbers are measured at the netem qdisc: bits/s sent, packets dropped (loss, queue overflow, blackhole),
        and bytes held (delayed or waiting for bandwidth). TCP connection details (RTT, cwnd, retransmissions) are in the node panel.
      </p>

      <h2>Live changes</h2>
      <p>
        From the Monitor tab (or <code>netsim shape</code>, <code>cut</code>, <code>heal</code>) you can switch a link's profile or
        override any field instantly. Overrides live in memory; saving the lab file and restarting applies file changes.
      </p>
    </div>
  );
}
