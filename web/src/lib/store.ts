import { create } from "zustand";
import { api } from "./api";
import type { LabSummary, ProfileSummary } from "./types";

export type TabKind = "home" | "lab" | "profile" | "help";
export interface Tab {
  id: string;
  kind: TabKind;
  name?: string;
}
export type Theme = "system" | "light" | "dark";

export interface Toast {
  id: number;
  level: "info" | "error" | "success";
  msg: string;
}

interface State {
  tabs: Tab[];
  active: string;
  labs: LabSummary[];
  profiles: ProfileSummary[];
  theme: Theme;
  dark: boolean;
  dirty: Record<string, boolean>;
  toasts: Toast[];
  info: { user: string; home: string; version: string } | null;
  openTab: (kind: TabKind, name?: string) => void;
  closeTab: (id: string) => void;
  renameTab: (kind: TabKind, from: string, to: string) => void;
  setActive: (id: string) => void;
  refreshLabs: () => Promise<void>;
  refreshProfiles: () => Promise<void>;
  setLabs: (labs: LabSummary[]) => void;
  setTheme: (t: Theme) => void;
  setDirty: (id: string, dirty: boolean) => void;
  toast: (level: Toast["level"], msg: string) => void;
  dismiss: (id: number) => void;
}

const tabId = (kind: TabKind, name?: string) => (name ? `${kind}:${name}` : kind);

function loadTabs(): { tabs: Tab[]; active: string } {
  try {
    const raw = JSON.parse(localStorage.getItem("netsim.tabs") || "null");
    if (raw?.tabs?.length) return raw;
  } catch {
    /* ignore */
  }
  return { tabs: [{ id: "home", kind: "home" }], active: "home" };
}

function saveTabs(tabs: Tab[], active: string) {
  localStorage.setItem("netsim.tabs", JSON.stringify({ tabs, active }));
}

function systemDark() {
  return matchMedia("(prefers-color-scheme: dark)").matches;
}

function applyTheme(t: Theme): boolean {
  const dark = t === "dark" || (t === "system" && systemDark());
  document.documentElement.dataset.theme = dark ? "dark" : "light";
  return dark;
}

let toastSeq = 0;
const initialTheme = (localStorage.getItem("netsim.theme") as Theme) || "system";

export const useStore = create<State>((set, get) => ({
  ...loadTabs(),
  labs: [],
  profiles: [],
  theme: initialTheme,
  dark: applyTheme(initialTheme),
  dirty: {},
  toasts: [],
  info: null,

  openTab(kind, name) {
    const id = tabId(kind, name);
    const { tabs } = get();
    const next = tabs.some((t) => t.id === id) ? tabs : [...tabs, { id, kind, name }];
    set({ tabs: next, active: id });
    saveTabs(next, id);
  },
  closeTab(id) {
    const { tabs, active, dirty } = get();
    if (dirty[id] && !confirm("Discard unsaved changes?")) return;
    const idx = tabs.findIndex((t) => t.id === id);
    const next = tabs.filter((t) => t.id !== id);
    const fallback = next[Math.min(idx, next.length - 1)]?.id ?? "home";
    const tabsFinal = next.length ? next : [{ id: "home", kind: "home" as const }];
    const nextActive = active === id ? fallback : active;
    const { [id]: _, ...rest } = dirty;
    set({ tabs: tabsFinal, active: nextActive, dirty: rest });
    saveTabs(tabsFinal, nextActive);
  },
  renameTab(kind, from, to) {
    const { tabs, active } = get();
    const oldId = tabId(kind, from);
    const newId = tabId(kind, to);
    const next = tabs.map((t) => (t.id === oldId ? { id: newId, kind, name: to } : t));
    const nextActive = active === oldId ? newId : active;
    set({ tabs: next, active: nextActive });
    saveTabs(next, nextActive);
  },
  setActive(id) {
    set({ active: id });
    saveTabs(get().tabs, id);
  },
  async refreshLabs() {
    set({ labs: await api.get<LabSummary[]>("/api/labs") });
  },
  async refreshProfiles() {
    set({ profiles: await api.get<ProfileSummary[]>("/api/profiles") });
  },
  setLabs(labs) {
    set({ labs });
  },
  setTheme(theme) {
    localStorage.setItem("netsim.theme", theme);
    set({ theme, dark: applyTheme(theme) });
  },
  setDirty(id, dirty) {
    if (!!get().dirty[id] === dirty) return;
    set({ dirty: { ...get().dirty, [id]: dirty } });
  },
  toast(level, msg) {
    const id = ++toastSeq;
    set({ toasts: [...get().toasts, { id, level, msg }] });
    setTimeout(() => get().dismiss(id), level === "error" ? 8000 : 3500);
  },
  dismiss(id) {
    set({ toasts: get().toasts.filter((t) => t.id !== id) });
  },
}));

matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
  const s = useStore.getState();
  if (s.theme === "system") useStore.setState({ dark: applyTheme("system") });
});

export function toastError(e: unknown) {
  useStore.getState().toast("error", e instanceof Error ? e.message : String(e));
}
