# SPDX-License-Identifier: LGPL-3.0-or-later
# Copyright (C) 2026 Eduardo Correia
"""Parsers for the text output of tcpdump, ping, iperf3 and ss."""

from __future__ import annotations

import re

_TCPDUMP_IP = re.compile(r"^(\d+\.\d+) (IP6?) (\S+) > (\S+?):? (.*)$")
_TCPDUMP_OTHER = re.compile(r"^(\d+\.\d+) (\S+?),? (.*)$")
_FLAGS = re.compile(r"Flags \[([^\]]*)\]")
_LENGTH = re.compile(r"length (\d+)")


def _split_addr(addr: str, v6: bool) -> tuple[str, int | None]:
    # tcpdump appends the port after a final dot: 10.0.0.1.5000, fe80::1.5353
    if v6 and "." in addr or not v6 and addr.count(".") == 4:
        host, port = addr.rsplit(".", 1)
        return host, int(port) if port.isdigit() else None
    return addr, None


def tcpdump_line(line: str) -> dict | None:
    m = _TCPDUMP_IP.match(line)
    if m:
        ts, fam, src, dst, info = m.groups()
        v6 = fam == "IP6"
        s_ip, s_port = _split_addr(src, v6)
        d_ip, d_port = _split_addr(dst, v6)
        if info.startswith("ICMP"):
            proto = "icmp6" if v6 else "icmp"
        elif _FLAGS.search(info):
            proto = "tcp"
        else:
            proto = "udp"
        lengths = _LENGTH.findall(info)
        flags = _FLAGS.search(info)
        return {"t": float(ts), "proto": proto, "src": s_ip, "sport": s_port, "dst": d_ip, "dport": d_port,
                "len": int(lengths[-1]) if lengths else None, "flags": flags.group(1) if flags else None,
                "info": info}
    m = _TCPDUMP_OTHER.match(line)
    if m:
        ts, proto, info = m.groups()
        lengths = _LENGTH.findall(info)
        return {"t": float(ts), "proto": proto.lower(), "src": None, "dst": None, "sport": None, "dport": None,
                "len": int(lengths[-1]) if lengths else None, "flags": None, "info": info}
    return None


_PING_OK = re.compile(r"icmp_seq=(\d+).*time=([\d.]+) ms")
_PING_LOST = re.compile(r"no answer yet for icmp_seq=(\d+)")


def ping_line(line: str) -> dict | None:
    if m := _PING_OK.search(line):
        return {"seq": int(m.group(1)), "rtt": float(m.group(2))}
    if m := _PING_LOST.search(line):
        return {"seq": int(m.group(1)), "rtt": None}
    return None


_IPERF = re.compile(
    r"^\[\s*(\d+|SUM)\]\s+([\d.]+)-([\d.]+)\s+sec\s+([\d.]+)\s+(\w?)Bytes\s+([\d.]+)\s+(\w?)bits/sec(.*)$")
_UNIT = {"": 1, "K": 1e3, "M": 1e6, "G": 1e9}


def iperf_line(line: str) -> dict | None:
    m = _IPERF.match(line.strip())
    if not m:
        return None
    _, start, end, _amount, _aunit, rate, runit, rest = m.groups()
    out = {"start": float(start), "end": float(end), "bps": float(rate) * _UNIT.get(runit, 1),
           "summary": "sender" in rest or "receiver" in rest,
           "role": "sender" if "sender" in rest else "receiver" if "receiver" in rest else None}
    tokens = rest.split()
    if tokens and tokens[0].isdigit():
        out["retr"] = int(tokens[0])
    if lost := re.search(r"(\d+)/(\d+) \(([\d.e+-]+)%\)", rest):
        out["lost"], out["total"], out["loss_pct"] = int(lost.group(1)), int(lost.group(2)), float(lost.group(3))
    if jit := re.search(r"([\d.]+) ms", rest):
        out["jitter_ms"] = float(jit.group(1))
    return out


_RATE_KV = ("send", "pacing_rate", "delivery_rate")


def ss_tcp_line(line: str) -> dict | None:
    tokens = line.split()
    if len(tokens) < 5:
        return None
    out: dict = {"state": tokens[0], "recvq": int(tokens[1]), "sendq": int(tokens[2]),
                 "local": tokens[3], "peer": tokens[4]}
    it = iter(tokens[5:])
    for tok in it:
        if tok in _RATE_KV:
            out[tok] = next(it, None)
        elif ":" in tok and not tok.startswith("users:"):
            k, v = tok.split(":", 1)
            if k == "rtt" and "/" in v:
                a, b = v.split("/")
                out["rtt"], out["rttvar"] = float(a), float(b)
            elif k == "retrans" and "/" in v:
                a, b = v.split("/")
                out["retrans"], out["retrans_total"] = int(a), int(b)
            elif k in ("cwnd", "ssthresh", "bytes_sent", "bytes_acked", "bytes_received", "bytes_retrans",
                       "segs_out", "segs_in", "lost", "unacked", "mss"):
                try:
                    out[k] = int(v)
                except ValueError:
                    pass
            elif k in ("minrtt", "rto"):
                try:
                    out[k] = float(v)
                except ValueError:
                    pass
        elif tok.startswith("users:"):
            if m := re.search(r'"([^"]+)",pid=(\d+)', tok):
                out["process"], out["pid"] = m.group(1), int(m.group(2))
    return out


def ss_listen_line(line: str) -> dict | None:
    tokens = line.split()
    if len(tokens) < 6:
        return None
    out = {"proto": tokens[0], "state": tokens[1], "local": tokens[4]}
    if len(tokens) > 6 and (m := re.search(r'"([^"]+)",pid=(\d+)', " ".join(tokens[6:]))):
        out["process"], out["pid"] = m.group(1), int(m.group(2))
    return out
