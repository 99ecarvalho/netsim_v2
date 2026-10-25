# Changelog

## 2.0.0

Rewrite. The original single-script version is replaced.

- Labs: topologies of hosts, routers, switches, this machine and Docker containers, in YAML.
- Profiles: 40 link profiles in YAML with inheritance, per-direction values and timelines.
- netem shaping per link direction; exponential, lognormal and weibull delay tables.
- `netsim run`, `pipe`, `exec`, proxies and `--forward` to put programs on a simulated network.
- Daemon with token-protected HTTP/WebSocket API.
- Web UI: graph + YAML editor, live monitor, packet capture, ping/iperf3 probes, terminals.
- `run.sh` launcher, Makefile, systemd unit.
