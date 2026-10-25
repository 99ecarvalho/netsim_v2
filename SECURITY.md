# Security

netsim runs a root daemon that creates namespaces, changes the firewall and starts programs.
Its HTTP API is protected by a random token in `/run/netsim/token` (mode 0600, owned by the user
who started it) and listens on 127.0.0.1 by default.

Treat the token like a sudo password. Do not expose the daemon with `--host 0.0.0.0` on an
untrusted network.

Report vulnerabilities privately to the maintainer by e-mail instead of opening a public issue.
Include the version (`netsim --version`), what you did and what happened.
