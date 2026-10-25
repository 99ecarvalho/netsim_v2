# netsim - build and install helpers
PY ?= python3

.PHONY: all web install dev serve test check clean service dist dist-check

all: web

web/node_modules: web/package.json web/package-lock.json
	cd web && npm ci
	touch web/node_modules

web: web/node_modules
	cd web && npm run build

# installs the `netsim` command for the current user (editable: uses this checkout's profiles/ and labs/)
install: web
	@if command -v uv >/dev/null; then uv tool install --force --editable .; \
	else pipx install --force --editable .; fi
	@echo "installed: $$(command -v netsim || echo '~/.local/bin/netsim')"

.venv:
	$(PY) -m venv .venv
	.venv/bin/pip install -q -e '.[dev]'

serve: .venv web
	.venv/bin/netsim serve

# UI with hot reload against a running daemon (netsim serve)
dev: web/node_modules
	@echo "open http://localhost:5173/?token=$$(cat /run/netsim/token)"
	cd web && npm run dev

test: .venv
	.venv/bin/pytest -q

check: test
	cd web && npx tsc -b

service:
	sed -e "s|@NETSIM@|$$(command -v netsim)|" -e "s|@USER@|$$USER|" contrib/netsim.service \
		| sudo tee /etc/systemd/system/netsim.service >/dev/null
	sudo systemctl daemon-reload
	sudo systemctl enable --now netsim
	@echo "netsim is running as a service; open it with: netsim ui"

# release artifacts: UI built first so the wheel carries it, then an install test in a clean venv
dist: web
	rm -rf dist build
	uv build --sdist --wheel -o dist .
	$(MAKE) dist-check

dist-check:
	@tmp=$$(mktemp -d) && trap 'rm -rf "$$tmp"' EXIT && \
	uv venv -q "$$tmp/venv" && uv pip install -q -p "$$tmp/venv/bin/python" dist/*.whl && \
	cd "$$tmp" && NETSIM_HOME="$$tmp/home" "$$tmp/venv/bin/netsim" profiles --offline >/dev/null && \
	n=$$(ls "$$tmp/home/profiles" | wc -l) && labs=$$(ls "$$tmp/home/labs" | wc -l) && \
	ui=$$("$$tmp/venv/bin/python" -c 'import netsim.config as c; print(c.web_dir() is not None)') && \
	echo "wheel: $$n profiles, $$labs labs, web UI: $$ui" && [ "$$n" -ge 40 ] && [ "$$labs" -ge 1 ] && [ "$$ui" = True ]

clean:
	rm -rf netsim/web web/node_modules .venv build dist *.egg-info
