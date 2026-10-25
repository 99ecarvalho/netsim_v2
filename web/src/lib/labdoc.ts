// SPDX-License-Identifier: LGPL-3.0-or-later
// Copyright (C) 2026 Eduardo Correia
/**
 * Edits to a lab expressed as mutations of its YAML document, so the text
 * editor and the graph editor share one source of truth and comments survive.
 */
import YAML, { Document, isMap, isSeq, YAMLMap, YAMLSeq } from "yaml";
import type { LabSpec, NodeKind } from "./types";

export interface ParsedLab {
  doc: Document;
  data: LabSpec;
  error: { msg: string; line?: number } | null;
}

export function parseLab(text: string): ParsedLab {
  const doc = YAML.parseDocument(text, { keepSourceTokens: false });
  if (doc.errors.length) {
    const e = doc.errors[0];
    return { doc, data: {}, error: { msg: e.message.split("\n")[0], line: e.linePos?.[0]?.line } };
  }
  const data = (doc.toJS() ?? {}) as LabSpec;
  if (typeof data !== "object" || Array.isArray(data)) {
    return { doc, data: {}, error: { msg: "top level must be a mapping" } };
  }
  const shape = checkShape(data as Record<string, unknown>);
  if (shape) {
    const line = lineOf(doc, text, shape.loc);
    return { doc, data: {}, error: { msg: `${shape.loc}: ${shape.msg}`, line } };
  }
  return { doc, data, error: null };
}

const isPlainMap = (v: unknown) => typeof v === "object" && v !== null && !Array.isArray(v);

/** The minimum structure the graph needs; the server validates everything else. */
function checkShape(d: Record<string, unknown>): { loc: string; msg: string } | null {
  if (d.nodes != null) {
    if (!isPlainMap(d.nodes)) return { loc: "nodes", msg: "must be a mapping of node names" };
    for (const [k, v] of Object.entries(d.nodes as object)) {
      if (v != null && !isPlainMap(v)) return { loc: `nodes.${k}`, msg: "must be a mapping (or empty)" };
    }
  }
  if (d.links != null) {
    if (!Array.isArray(d.links)) return { loc: "links", msg: "must be a list" };
    for (const [i, l] of (d.links as unknown[]).entries()) {
      if (!isPlainMap(l)) return { loc: `links.${i}`, msg: "must be a mapping like {a: x, b: y}" };
      const link = l as Record<string, unknown>;
      if (typeof link.a !== "string" || typeof link.b !== "string") return { loc: `links.${i}`, msg: "needs a and b" };
    }
  }
  for (const key of ["programs", "proxies"]) {
    const v = d[key];
    if (v == null) continue;
    if (!Array.isArray(v)) return { loc: key, msg: "must be a list" };
    for (const [i, item] of v.entries()) if (!isPlainMap(item)) return { loc: `${key}.${i}`, msg: "must be a mapping" };
  }
  if (d.layout != null) {
    if (!isPlainMap(d.layout)) return { loc: "layout", msg: "must be a mapping" };
    for (const [k, p] of Object.entries(d.layout as object)) {
      const pos = p as Record<string, unknown>;
      if (!isPlainMap(p) || typeof pos.x !== "number" || typeof pos.y !== "number") {
        return { loc: `layout.${k}`, msg: "must be {x: number, y: number}" };
      }
    }
  }
  return null;
}

export function serialize(doc: Document): string {
  return doc.toString({ lineWidth: 80, flowCollectionPadding: false });
}

function ensureMap(doc: Document, key: string): YAMLMap {
  let node = doc.get(key, true);
  if (!isMap(node)) {
    node = doc.createNode({}) as YAMLMap;
    doc.set(key, node);
  }
  return node as YAMLMap;
}

function ensureSeq(doc: Document, key: string): YAMLSeq {
  let node = doc.get(key, true);
  if (!isSeq(node)) {
    node = doc.createNode([]) as YAMLSeq;
    doc.set(key, node);
  }
  return node as YAMLSeq;
}

function flow(doc: Document, value: object) {
  return doc.createNode(value, { flow: true });
}

const KIND_PREFIX: Record<NodeKind, string> = { host: "host", router: "router", switch: "switch", external: "host", container: "container" };

export function freeName(data: LabSpec, kind: NodeKind): string {
  if (kind === "external" && !data.nodes?.host) return "host";
  const base = kind === "external" ? "internet" : KIND_PREFIX[kind];
  for (let i = 1; ; i++) {
    const n = `${base}${i}`;
    if (!data.nodes?.[n]) return n;
  }
}

export function addNode(doc: Document, name: string, kind: NodeKind, pos?: { x: number; y: number }) {
  const nodes = ensureMap(doc, "nodes");
  const spec: Record<string, unknown> = kind === "host" ? {} : { kind };
  if (kind === "external") spec.internet = true;
  if (kind === "container") spec.container = "my-container";
  nodes.set(name, flow(doc, spec));
  if (pos) setLayout(doc, { [name]: pos });
}

export function removeNode(doc: Document, name: string) {
  doc.deleteIn(["nodes", name]);
  doc.deleteIn(["layout", name]);
  for (const key of ["links", "programs", "proxies"]) {
    const seq = doc.get(key, true);
    if (!isSeq(seq)) continue;
    for (let i = seq.items.length - 1; i >= 0; i--) {
      const item = (seq.items[i] as YAMLMap).toJSON?.() ?? {};
      const refs = key === "links" ? [item.a, item.b]
        : key === "programs" ? [item.node]
        : [item.listen_node, item.target_node, ...(item.via ?? [])];
      if (refs.includes(name)) seq.items.splice(i, 1);
    }
  }
}

export function renameNode(doc: Document, from: string, to: string) {
  if (from === to) return;
  const nodes = ensureMap(doc, "nodes");
  const pair = nodes.items.find((p) => String((p.key as any)?.value ?? p.key) === from);
  if (!pair) return;
  pair.key = doc.createNode(to);
  const layout = doc.get("layout", true);
  if (isMap(layout)) {
    const lp = layout.items.find((p) => String((p.key as any)?.value ?? p.key) === from);
    if (lp) lp.key = doc.createNode(to);
  }
  const swap = (path: (string | number)[]) => {
    if (doc.getIn(path) === from) doc.setIn(path, to);
  };
  const each = (key: string, fields: string[]) => {
    const seq = doc.get(key, true);
    if (!isSeq(seq)) return;
    seq.items.forEach((_, i) => {
      fields.forEach((f) => swap([key, i, f]));
      if (key === "proxies") [0, 1].forEach((j) => swap([key, i, "via", j]));
    });
  };
  each("links", ["a", "b"]);
  each("programs", ["node"]);
  each("proxies", ["listen_node", "target_node"]);
}

export function setNodeField(doc: Document, name: string, key: string, value: unknown) {
  const node = doc.getIn(["nodes", name], true);
  if (!isMap(node)) doc.setIn(["nodes", name], flow(doc, {}));
  if (value === undefined || value === "" || value === null || value === false) doc.deleteIn(["nodes", name, key]);
  else doc.setIn(["nodes", name, key], value);
}

export function addLink(doc: Document, a: string, b: string, profile?: string): number {
  const links = ensureSeq(doc, "links");
  links.add(flow(doc, profile ? { a, b, profile } : { a, b }));
  return links.items.length - 1;
}

export function removeLink(doc: Document, index: number) {
  const links = doc.get("links", true);
  if (isSeq(links)) links.items.splice(index, 1);
}

export function setIn(doc: Document, path: (string | number)[], value: unknown) {
  if (value === undefined || value === "" || value === null) doc.deleteIn(path);
  else if (typeof value === "object" && !Array.isArray(value)) doc.setIn(path, flow(doc, value));
  else doc.setIn(path, value);
}

export function setLayout(doc: Document, positions: Record<string, { x: number; y: number }>) {
  const layout = ensureMap(doc, "layout");
  for (const [name, p] of Object.entries(positions)) {
    layout.set(name, flow(doc, { x: Math.round(p.x), y: Math.round(p.y) }));
  }
}

export function appendItem(doc: Document, key: "programs" | "proxies", value: object) {
  ensureSeq(doc, key).add(flow(doc, value));
}

export function removeItem(doc: Document, key: "programs" | "proxies", index: number) {
  const seq = doc.get(key, true);
  if (isSeq(seq)) seq.items.splice(index, 1);
}

/** Line (1-based) of a validation location such as "links.2.profile". */
export function lineOf(doc: Document, text: string, loc: string): number | undefined {
  const path = loc.split(".").filter(Boolean).map((p) => (/^\d+$/.test(p) ? Number(p) : p));
  while (path.length) {
    const node = doc.getIn(path, true) as { range?: [number, number, number] } | undefined;
    if (node && node.range) return text.slice(0, node.range[0]).split("\n").length;
    path.pop();
  }
  return undefined;
}

export const NEW_LAB = `description: New lab
nodes:
  client: {icon: laptop}
  server: {icon: server}
links:
  - {a: client, b: server, profile: 4g-lte}
`;

export const NEW_PROFILE = `title: My profile
description: What this link looks like and when to use it.
category: custom
delay: 40ms
jitter: 10ms
distribution: normal
loss: 0.5%
up: {rate: 5mbit}
down: {rate: 20mbit}
`;
