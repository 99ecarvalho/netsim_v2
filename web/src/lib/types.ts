export type NodeKind = "host" | "router" | "switch" | "external";
export type Direction = "up" | "down";

export interface LabSummary {
  name: string;
  state: "stopped" | "starting" | "running" | "stopping" | "failed";
  ephemeral: boolean;
  error?: string | null;
  description?: string | null;
  invalid?: boolean;
  modified?: boolean;
}

export interface ProfileSummary {
  name: string;
  title?: string | null;
  description?: string | null;
  category?: string | null;
  tags?: string[];
  extends?: string | null;
  timeline?: number;
  summary?: { up: string; down: string };
  error?: string | null;
}

export interface LabNodeSpec {
  kind?: NodeKind;
  description?: string;
  icon?: string;
  internet?: boolean;
  subnet?: string;
  sysctl?: Record<string, string | number>;
}

export interface LinkSpec {
  a: string;
  b: string;
  name?: string;
  profile?: string;
  subnet?: string;
  ip_a?: string;
  ip_b?: string;
  mtu?: number;
  up?: Record<string, unknown>;
  down?: Record<string, unknown>;
  [k: string]: unknown;
}

export interface ProgramSpec {
  name: string;
  node: string;
  cmd: string;
  cwd?: string;
  user?: string;
  env?: Record<string, string>;
  autostart?: boolean;
  restart?: "no" | "on-failure" | "always";
  delay?: string;
}

export interface ProxySpec {
  name: string;
  proto?: "tcp" | "udp";
  listen: string;
  listen_node?: string;
  target: string;
  target_node?: string;
  via?: [string, string];
  enabled?: boolean;
}

export interface LabSpec {
  description?: string;
  subnet?: string;
  nodes?: Record<string, LabNodeSpec | null>;
  links?: LinkSpec[];
  programs?: ProgramSpec[];
  proxies?: ProxySpec[];
  layout?: Record<string, { x: number; y: number }>;
}

export interface PlanIface {
  node: string;
  name: string;
  link: string;
  side: "a" | "b";
  peer: string;
  ip: string | null;
  mtu: number | null;
}

export interface PlanNode {
  name: string;
  kind: NodeKind;
  ns: string | null;
  ip: string | null;
  ifaces: PlanIface[];
  routes: { dst: string; via: string; dev: string }[];
}

export interface PlanLink {
  id: string;
  a: PlanIface;
  b: PlanIface;
  segment: number;
  shaping?: { up: string; down: string };
}

export interface Plan {
  lab: string;
  base: string;
  external: string | null;
  internet: boolean;
  nodes: Record<string, PlanNode>;
  links: Record<string, PlanLink>;
  segments: { index: number; subnet: string; switches: string[]; members: { node: string; iface: string; ip: string }[] }[];
  hosts: Record<string, string>;
}

export interface AppliedDir {
  summary: string;
  args: string[];
}

export interface LinkRuntime {
  profile: string | null;
  runtime: Record<Direction, Record<string, unknown>>;
  timeline: boolean;
  step: number | null;
  step_label: string | null;
  step_remaining: number | null;
  up: AppliedDir | null;
  down: AppliedDir | null;
}

export interface ProgramRuntime {
  name: string;
  node: string;
  cmd: string;
  status: "stopped" | "starting" | "running" | "exited" | "failed";
  exit_code: number | null;
  started_at: number | null;
  restarts: number;
  pid: number | null;
  autostart: boolean;
  restart: string;
  user: string | null;
}

export interface ProxyStats {
  active: number;
  total: number;
  errors: number;
  bytes_up: number;
  bytes_down: number;
  last_error: string | null;
}

export interface ProxyRuntime {
  name: string;
  proto: string;
  listen: string;
  listen_node: string | null;
  target: string;
  target_node: string | null;
  route: string | null;
  running: boolean;
  error: string | null;
  stats: ProxyStats | null;
}

export interface CaptureInfo {
  id: string;
  lab: string;
  link: string;
  side: string;
  node: string;
  iface: string;
  filter: string;
  started: number;
  packets: number;
  running: boolean;
  size: number;
}

export interface ProbeInfo {
  id: string;
  lab: string;
  kind: "ping" | "iperf";
  src: string;
  dst: string;
  options: Record<string, unknown>;
  started: number;
  running: boolean;
  results?: ProbeResult[];
}

export interface ProbeResult {
  t: number;
  seq?: number;
  rtt?: number | null;
  bps?: number;
  start?: number;
  end?: number;
  summary?: boolean;
  role?: string | null;
  retr?: number;
  loss_pct?: number;
  jitter_ms?: number;
  error?: string;
  done?: boolean;
}

export interface Runtime {
  name: string;
  state: string;
  started_at: number;
  error: string | null;
  plan: Plan;
  lab: LabSpec;
  links: Record<string, LinkRuntime>;
  programs: Record<string, ProgramRuntime>;
  proxies: Record<string, ProxyRuntime>;
  captures: CaptureInfo[];
  probes: ProbeInfo[];
  modified: boolean;
}

export interface DirStats {
  bytes: number;
  packets: number;
  drops: number;
  backlog: number;
  qlen: number;
  bps: number;
  pps: number;
  dps: number;
}

export interface StatsSample {
  t: number;
  links: Record<string, Partial<Record<Direction, DirStats>>>;
  proxies: Record<string, ProxyStats>;
}

export interface LabEvent {
  t: number;
  level: "info" | "warn" | "error";
  msg: string;
}

export interface CommandRecord {
  t: number;
  cmd: string;
  code: number;
  stderr: string;
  input: string | null;
}

export interface Packet {
  t: number;
  proto: string;
  src: string | null;
  sport: number | null;
  dst: string | null;
  dport: number | null;
  len: number | null;
  flags: string | null;
  info: string;
  dir?: "in" | "out";
}

export interface ValidationDetail {
  loc: string;
  msg: string;
}
