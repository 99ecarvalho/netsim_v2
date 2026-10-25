"""Generate netem delay distribution tables.

iproute2 ships normal, pareto, paretonormal and experimental tables. We generate
exponential, lognormal and weibull ones so profiles can use them.

Table format (same as iproute2's maketable): 4096 samples of the inverse CDF of a
*standardised* variable (mean 0, std-dev 1), scaled by NETEM_DIST_SCALE=8192 and
clamped to int16. netem computes delay = mean + jitter * sample / 8192, so for
every table `delay` is the mean and `jitter` the standard deviation.
"""

from __future__ import annotations

import math
import os
from pathlib import Path

TABLE_SIZE = 4096
SCALE = 8192
SYSTEM_DIRS = ("/usr/lib/tc", "/usr/lib/x86_64-linux-gnu/tc", "/usr/lib/aarch64-linux-gnu/tc", "/usr/lib64/tc")
BUILTIN = ("normal", "pareto", "paretonormal", "experimental")

LOGNORMAL_SIGMA = 0.6  # shape of the underlying normal; ~2.2 skewness
WEIBULL_K = 1.5


def _exponential(u: float) -> float:
    return -math.log1p(-u) - 1.0  # Exp(1) has mean 1, std 1


def _lognormal(u: float) -> float:
    s = LOGNORMAL_SIGMA
    x = math.exp(s * _norm_ppf(u))
    mean = math.exp(s * s / 2)
    std = math.sqrt((math.exp(s * s) - 1) * math.exp(s * s))
    return (x - mean) / std


def _weibull(u: float) -> float:
    k = WEIBULL_K
    x = (-math.log1p(-u)) ** (1 / k)
    mean = math.gamma(1 + 1 / k)
    std = math.sqrt(math.gamma(1 + 2 / k) - mean**2)
    return (x - mean) / std


def _norm_ppf(p: float) -> float:
    """Inverse standard normal CDF (Acklam's approximation, |err| < 1.2e-9)."""
    a = (-3.969683028665376e01, 2.209460984245205e02, -2.759285104469687e02,
         1.383577518672690e02, -3.066479806614716e01, 2.506628277459239e00)
    b = (-5.447609879822406e01, 1.615858368580409e02, -1.556989798598866e02,
         6.680131188771972e01, -1.328068155288572e01)
    c = (-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e00,
         -2.549732539343734e00, 4.374664141464968e00, 2.938163982698783e00)
    d = (7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e00, 3.754408661907416e00)
    lo, hi = 0.02425, 1 - 0.02425
    if p < lo:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
               ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    if p > hi:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
                ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    q = p - 0.5
    r = q * q
    return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / \
           (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)


GENERATED = {"exponential": _exponential, "lognormal": _lognormal, "weibull": _weibull}


def table(name: str) -> list[int]:
    fn = GENERATED[name]
    out = []
    for i in range(TABLE_SIZE):
        z = fn((i + 0.5) / TABLE_SIZE)
        out.append(max(-32768, min(32767, round(z * SCALE))))
    return out


def render(name: str) -> str:
    values = table(name)
    lines = [f"# netsim generated {name} distribution table (standardised, scale {SCALE})"]
    for i in range(0, len(values), 8):
        lines.append(" " + " ".join(f"{v:6d}" for v in values[i:i + 8]))
    return "\n".join(lines) + "\n"


def system_dir() -> Path | None:
    for d in SYSTEM_DIRS:
        if os.path.exists(os.path.join(d, "normal.dist")):
            return Path(d)
    return None


def install(target: Path) -> Path:
    """Populate `target` with all tables; use it as TC_LIB_DIR."""
    target.mkdir(parents=True, exist_ok=True)
    sysdir = system_dir()
    for name in BUILTIN:
        link = target / f"{name}.dist"
        if sysdir and not link.exists():
            link.symlink_to(sysdir / f"{name}.dist")
    for name in GENERATED:
        path = target / f"{name}.dist"
        if not path.exists():
            path.write_text(render(name))
    return target
