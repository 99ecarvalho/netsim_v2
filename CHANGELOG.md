# Changelog

## 2.0.0

First public release.

- Labs: topologies of hosts, routers, switches, this machine and Docker containers, in YAML.
- Profiles: 40 link profiles in YAML with inheritance, per-direction values and timelines.
- netem shaping per link direction; exponential, lognormal and weibull delay tables.
- `netsim run`, `pipe`, `exec`, proxies and `--forward` to put programs on a simulated network.
- Daemon with token-protected HTTP/WebSocket API.
- Web UI: graph + YAML editor, live monitor, packet capture, ping/iperf3 probes, terminals.
- Live link changes; saved shaping changes apply to running labs without restart.
- `run.sh` launcher, Makefile, systemd unit.
