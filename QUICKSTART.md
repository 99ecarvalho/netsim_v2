# Quickstart

Copy, paste, watch. Each block is independent. Details are in [README.md](README.md).

## 0. Start

```sh
./run.sh                                   # builds if needed, starts the daemon, prints the UI link
export PATH="$PWD/.venv/bin:$PATH"         # puts `netsim` on your PATH for this shell
```

Open the printed `http://127.0.0.1:8077/?token=...` link in a browser. Lost it? Run `./run.sh status`.

## 1. Same request, different networks

```sh
netsim run -p ideal     -- curl -so /dev/null -w '%{time_total}s\n' https://example.com
netsim run -p 3g-hspa   -- curl -so /dev/null -w '%{time_total}s\n' https://example.com
netsim run -p gprs      -- curl -so /dev/null -w '%{time_total}s\n' https://example.com
```

## 2. Feel a satellite link from a shell

```sh
netsim run -p satellite-geo -- bash        # everything started in this shell goes through the link
ping -c 5 1.1.1.1                          # ~600 ms
exit
```

## 3. A link that changes over time

```sh
netsim run -p gsm-flaky -- ping 1.1.1.1    # 3G, then outages, then EDGE... Ctrl-C to stop
```

While it runs, open the `run-…` lab in the UI → **Monitor** → click the link to see the timeline.

## 4. Slow down a local client/server without changing them

```sh
python3 -m http.server 5000 &              # your "server"
netsim pipe 6000:5000 -p edge              # in another terminal
curl -so /dev/null -w '%{time_total}s\n' http://localhost:6000/   # via EDGE
curl -so /dev/null -w '%{time_total}s\n' http://localhost:5000/   # direct
```

## 5. A client that hardcodes localhost

```sh
python3 -m http.server 5000 &
netsim run -p 4g-lte-poor --forward 5000 -- curl -s http://localhost:5000/ | head -3
```

`localhost:5000` inside the sandbox reaches the real `localhost:5000` through the shaped link.

## 6. A Docker server, 3G vs Ethernet, side by side

```sh
docker run -d --rm --name my-api -p 8088:80 nginx:alpine

netsim run -p lan-gigabit -- curl -so /dev/null -w 'ethernet %{time_total}s\n' http://host:8088/ &
netsim run -p 3g-hspa     -- curl -so /dev/null -w '3g       %{time_total}s\n' http://host:8088/ &
wait
```

Or with the container inside a lab (clients reach it as `api`):

```sh
netsim up docker-differential
netsim exec docker-differential client-eth -- curl -so /dev/null -w 'ethernet %{time_total}s\n' http://api/
netsim exec docker-differential client-3g  -- curl -so /dev/null -w '3g       %{time_total}s\n' http://api/
netsim down docker-differential
docker stop my-api
```

## 7. A full scenario in the UI

```sh
netsim up iot-gateway
```

In the UI open **iot-gateway**:

- **Monitor**: live graph; click the `uplink` edge for charts and live controls (profile, delay, loss, cut).
- **Traffic**: start a capture on `uplink`, run a ping probe `sensor1 → cloud`, download the `.pcap`.
- **Terminal**: open a shell on `sensor1` and try `curl http://cloud:8080/`.
- **Design**: drag nodes, add links, see the YAML change next to the graph.

The same from the terminal:

```sh
netsim exec iot-gateway sensor1 -- curl -so /dev/null -w '%{time_total}s\n' http://cloud:8080/
netsim cut  iot-gateway uplink              # cable pulled
netsim exec iot-gateway sensor1 -- curl -m 3 http://cloud:8080/   # times out
netsim heal iot-gateway uplink
netsim shape iot-gateway uplink --delay 400ms --loss 5%           # live change
netsim status iot-gateway
netsim down iot-gateway
```

## 8. Measure a profile

```sh
netsim up client-server
netsim exec client-server client -- ping -c 10 -i 0.2 server
netsim exec client-server server -- iperf3 -s -1 -D
netsim exec client-server client -- iperf3 -c server -t 5      # ~10 Mbit/s
netsim exec client-server server -- iperf3 -s -1 -D
netsim exec client-server client -- iperf3 -c server -t 5 -R   # also ~10 Mbit/s, not 50
netsim down client-server
```

The 4g-lte profile allows 15/50 Mbit/s, but one TCP connection gets ~10: with 45 ms RTT and 0.1% loss,
TCP itself is the limit. Try `netsim shape client-server client--server --loss 0%` and run again.

## 9. Make your own profile

```sh
netsim profiles                                     # what exists
netsim profile 4g-lte                               # its YAML and the exact tc commands
cp profiles/4g-lte.yaml profiles/my-link.yaml       # edit delay/rate/loss...
netsim run -p my-link -- curl -sI https://example.com
```

Or in the UI: sidebar → Profiles → **+**.

## Stop

```sh
./run.sh stop        # stops the daemon and removes every lab
```
