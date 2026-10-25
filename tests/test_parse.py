# SPDX-License-Identifier: LGPL-3.0-or-later
# Copyright (C) 2026 Eduardo Correia
from netsim import parse


def test_tcpdump():
    p = parse.tcpdump_line("1729849392.123456 IP 10.201.0.1.54321 > 10.201.0.2.5000: "
                           "Flags [S], seq 1, win 64240, length 0")
    assert p["proto"] == "tcp" and p["sport"] == 54321 and p["dst"] == "10.201.0.2" and p["flags"] == "S"
    p = parse.tcpdump_line("1729849392.1 IP 10.201.0.1 > 10.201.0.2: ICMP echo request, id 1, seq 1, length 64")
    assert p["proto"] == "icmp" and p["sport"] is None and p["len"] == 64
    p = parse.tcpdump_line("1729849392.1 IP 10.201.0.1.40000 > 10.201.0.2.53: UDP, length 30")
    assert p["proto"] == "udp" and p["dport"] == 53
    p = parse.tcpdump_line("1729849392.1 ARP, Request who-has 10.201.0.2 tell 10.201.0.1, length 28")
    assert p["proto"] == "arp"


def test_ping_iperf_ss():
    assert parse.ping_line("64 bytes from 10.0.0.2: icmp_seq=3 ttl=64 time=40.3 ms") == {"seq": 3, "rtt": 40.3}
    assert parse.ping_line("no answer yet for icmp_seq=5") == {"seq": 5, "rtt": None}
    r = parse.iperf_line("[  5]   0.00-1.00   sec  1.12 MBytes  9.42 Mbits/sec    2   56.6 KBytes")
    assert r["bps"] == 9.42e6 and r["retr"] == 2 and not r["summary"]
    r = parse.iperf_line("[  5]   0.00-10.00  sec  1.25 MBytes  1.05 Mbits/sec  0.123 ms  3/905 (0.33%)  receiver")
    assert r["summary"] and r["lost"] == 3 and r["jitter_ms"] == 0.123
    s = parse.ss_tcp_line("ESTAB 0 0 10.0.0.1:43210 10.0.0.2:5000 cubic rto:244 rtt:41.2/10.3 cwnd:10 "
                          "bytes_sent:100 send 2.81Mbps retrans:0/2 users:((\"curl\",pid=12,fd=3))")
    assert s["rtt"] == 41.2 and s["cwnd"] == 10 and s["retrans_total"] == 2 and s["send"] == "2.81Mbps"
    assert s["process"] == "curl"
