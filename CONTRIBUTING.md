# Contributing

Bug reports, profiles and fixes are welcome.

## Setup

```sh
./run.sh build          # python venv + web UI
make test               # unit tests, no root needed
make dev                # UI with hot reload against a running daemon (./run.sh)
```

## Before a pull request

- `make check` passes (pytest + TypeScript).
- One topic per pull request. Commits follow [Conventional Commits](https://www.conventionalcommits.org/)
  (`feat(engine): ...`, `fix(web): ...`, `docs: ...`).
- New profiles: say where the numbers come from (measurement, spec, operator data) in `description`.
  Delays are one-way. Rates are bits/s.
- Changes that touch the host (iptables, nft, sysctl, interfaces) must be undone on lab stop and by
  `netsim cleanup`.
- New source files start with:
  ```
  # SPDX-License-Identifier: LGPL-3.0-or-later
  ```

By contributing you agree that your contribution is licensed under LGPL-3.0-or-later.
