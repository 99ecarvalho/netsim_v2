// SPDX-License-Identifier: LGPL-3.0-or-later
// Copyright (C) 2026 Eduardo Correia
import { useEffect, useRef, useSyncExternalStore } from "react";
import { wsUrl } from "./api";

type Handler = (data: any, topic: string) => void;

function match(pattern: string, topic: string): boolean {
  if (!pattern.includes("*")) return pattern === topic;
  const re = new RegExp("^" + pattern.split("*").map(escape).join(".*") + "$");
  return re.test(topic);
}

function escape(s: string) {
  return s.replace(/[.+?^${}()|[\]\\]/g, "\\$&");
}

/** One multiplexed websocket; components subscribe to topic patterns. */
class EventSocket {
  private ws: WebSocket | null = null;
  private handlers = new Map<string, Set<Handler>>();
  private status: "connecting" | "open" | "closed" = "connecting";
  private listeners = new Set<() => void>();
  private retry = 0;
  private reconnectHooks = new Set<() => void>();

  constructor() {
    this.connect();
  }

  private setStatus(s: typeof this.status) {
    this.status = s;
    this.listeners.forEach((l) => l());
  }

  private connect() {
    this.setStatus("connecting");
    const ws = new WebSocket(wsUrl("/api/ws"));
    this.ws = ws;
    ws.onopen = () => {
      this.retry = 0;
      this.setStatus("open");
      const topics = [...this.handlers.keys()];
      if (topics.length) ws.send(JSON.stringify({ op: "sub", topics }));
      this.reconnectHooks.forEach((h) => h());
    };
    ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data);
      for (const [pattern, hs] of this.handlers) {
        if (match(pattern, msg.topic)) hs.forEach((h) => h(msg.data, msg.topic));
      }
    };
    ws.onclose = () => {
      this.setStatus("closed");
      this.retry = Math.min(this.retry + 1, 6);
      setTimeout(() => this.connect(), 500 * this.retry);
    };
  }

  subscribe(pattern: string, h: Handler): () => void {
    let set = this.handlers.get(pattern);
    if (!set) {
      set = new Set();
      this.handlers.set(pattern, set);
      if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify({ op: "sub", topics: [pattern] }));
    }
    set.add(h);
    return () => {
      set!.delete(h);
      if (set!.size === 0) {
        this.handlers.delete(pattern);
        if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify({ op: "unsub", topics: [pattern] }));
      }
    };
  }

  onReconnect(h: () => void): () => void {
    this.reconnectHooks.add(h);
    return () => this.reconnectHooks.delete(h);
  }

  getStatus = () => this.status;
  subscribeStatus = (l: () => void) => {
    this.listeners.add(l);
    return () => this.listeners.delete(l);
  };
}

export const events = new EventSocket();

/** Subscribe to a topic pattern for the component lifetime. Handler may change freely. */
export function useTopic(pattern: string | null, handler: Handler) {
  const ref = useRef(handler);
  ref.current = handler;
  useEffect(() => {
    if (!pattern) return;
    return events.subscribe(pattern, (d, t) => ref.current(d, t));
  }, [pattern]);
}

export function useReconnect(handler: () => void) {
  const ref = useRef(handler);
  ref.current = handler;
  useEffect(() => events.onReconnect(() => ref.current()), []);
}

export function useSocketStatus() {
  return useSyncExternalStore(events.subscribeStatus, events.getStatus);
}
