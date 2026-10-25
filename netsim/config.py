# SPDX-License-Identifier: LGPL-3.0-or-later
# Copyright (C) 2026 Eduardo Correia
"""Paths and identity of the invoking user."""

from __future__ import annotations

import fcntl
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
    """The human behind sudo (or NETSIM_USER), so files and programs are not owned by root."""
    if name := os.environ.get("NETSIM_USER"):
        return lookup_user(name)
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


def defaults_dir() -> Path | None:
    """The shipped profile/lab library: inside the package, or the checkout itself."""
    packaged = PACKAGE_DIR / "defaults"
    if (packaged / "profiles").is_dir():
        return packaged
    checkout = PACKAGE_DIR.parent
    return checkout if (checkout / "profiles").is_dir() else None


def seed_home(home: Path) -> list[str]:
    """Copy the shipped profiles and labs into a new home. Never overwrites: a
    directory that already exists is left alone, so deleted examples stay deleted."""
    src = defaults_dir()
    if src is None or src.resolve() == home.resolve():
        return []
    copied = []
    for kind in ("profiles", "labs"):
        target = home / kind
        if target.exists() or not (src / kind).is_dir():
            continue
        target.mkdir(parents=True)
        for parent in (home, target):
            chown_to_user(parent)
        for f in sorted((src / kind).glob("*.yaml")):
            (target / f.name).write_bytes(f.read_bytes())
            chown_to_user(target / f.name)
            copied.append(f"{kind}/{f.name}")
    return copied


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


class InstanceRunning(RuntimeError):
    def __init__(self, pid: str):
        super().__init__(f"another netsim daemon is running (pid {pid or '?'})")
        self.pid = pid


# Host-wide on purpose: namespaces, interfaces and firewall rules belong to the host, not to
# a run directory, so NETSIM_RUN_DIR must not be able to create a second owner.
LOCK_PATH = Path("/run/lock/netsim.lock") if Path("/run/lock").is_dir() else Path("/run/netsim.lock")


def acquire_instance_lock(lock_path: Path | None = None) -> int:
    """Exclusive, host-wide lock for whatever owns the host's netsim resources.

    Taken before any side effect (token, cleanup, endpoint), so a second daemon -
    or `netsim cleanup` while a daemon runs - refuses without touching anything.
    The lock is released by the kernel when the process exits, even on a crash.
    """
    path = lock_path or LOCK_PATH
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        pid = os.pread(fd, 32, 0).decode(errors="replace").strip()
        os.close(fd)
        raise InstanceRunning(pid) from None
    os.ftruncate(fd, 0)
    os.pwrite(fd, f"{os.getpid()}\n".encode(), 0)
    return fd
