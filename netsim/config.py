"""Paths and identity of the invoking user."""

from __future__ import annotations

import os
import pwd
from dataclasses import dataclass
from pathlib import Path

DEFAULT_PORT = 8077
RUN_DIR = Path(os.environ.get("NETSIM_RUN_DIR", "/run/netsim"))
PACKAGE_DIR = Path(__file__).resolve().parent


@dataclass(frozen=True)
class User:
    uid: int
    gid: int
    name: str
    home: str


def invoking_user() -> User:
    """The human behind sudo, so files and programs are not owned by root."""
    uid = int(os.environ.get("SUDO_UID", os.getuid()))
    gid = int(os.environ.get("SUDO_GID", os.getgid()))
    try:
        pw = pwd.getpwuid(uid)
        return User(uid, gid, pw.pw_name, pw.pw_dir)
    except KeyError:
        return User(uid, gid, str(uid), "/")


def lookup_user(name: str) -> User:
    pw = pwd.getpwnam(name)
    return User(pw.pw_uid, pw.pw_gid, pw.pw_name, pw.pw_dir)


def home_dir() -> Path:
    """Where profiles/ and labs/ live.

    NETSIM_HOME wins; a source checkout uses its own directory; otherwise
    ~/.config/netsim of the invoking user.
    """
    if env := os.environ.get("NETSIM_HOME"):
        return Path(env).expanduser()
    checkout = PACKAGE_DIR.parent
    if (checkout / "profiles").is_dir() and (checkout / "pyproject.toml").exists():
        return checkout
    return Path(invoking_user().home) / ".config" / "netsim"


def web_dir() -> Path | None:
    d = PACKAGE_DIR / "web"
    return d if (d / "index.html").exists() else None


def chown_to_user(path: Path) -> None:
    if os.geteuid() != 0:
        return
    u = invoking_user()
    try:
        os.chown(path, u.uid, u.gid)
    except OSError:
        pass
