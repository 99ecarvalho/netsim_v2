# SPDX-License-Identifier: LGPL-3.0-or-later
# Copyright (C) 2026 Eduardo Correia
"""Packaging hook: ship the profile and lab library inside the package.

profiles/ and labs/ live at the top of the repository so they are easy to find
and edit; the wheel carries a copy under netsim/defaults/, which seeds
~/.config/netsim on first run of an installed (non-checkout) netsim.
"""

import shutil
from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py

ROOT = Path(__file__).resolve().parent


class BuildWithLibrary(build_py):
    def run(self):
        super().run()
        for kind in ("profiles", "labs"):
            src = ROOT / kind
            dst = Path(self.build_lib) / "netsim" / "defaults" / kind
            if not src.is_dir():
                continue
            dst.mkdir(parents=True, exist_ok=True)
            for f in src.glob("*.yaml"):
                shutil.copy2(f, dst / f.name)
        if not (ROOT / "netsim" / "web" / "index.html").exists():
            self.warn("netsim/web is not built: the package will have no web UI (run `make web` first)")


setup(cmdclass={"build_py": BuildWithLibrary})
