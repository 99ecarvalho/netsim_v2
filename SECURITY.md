# Security

netsim runs a root daemon that creates namespaces, changes the firewall and starts programs.
Its HTTP API is protected by a random token in `/run/netsim/token` (mode 0600, owned by the user
who started it) and listens on 127.0.0.1 by default. The token is printed only on a terminal,
never to logs.

Treat the token like a sudo password. Do not expose the daemon with `--host 0.0.0.0` on an
untrusted network.

## Supported versions

Security fixes go into the latest release only.

## Reporting a vulnerability

Report privately through GitHub: open the repository's **Security** tab and choose
**Report a vulnerability** (https://github.com/99ecarvalho/netsim_v2/security/advisories/new).
Please do not open a public issue.

Include the version (`netsim --version`), what you did and what happened. You will get an answer
within a week; a fix and an advisory follow once the issue is confirmed.
