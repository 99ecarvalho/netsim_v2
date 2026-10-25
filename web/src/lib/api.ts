import type { ValidationDetail } from "./types";

const TOKEN_KEY = "netsim.token";

// The daemon hands the token over once via ?token=...; production builds turn it
// into an http-only cookie server side, the dev server needs it kept here.
export function initToken(): void {
  const url = new URL(location.href);
  const t = url.searchParams.get("token");
  if (t) {
    localStorage.setItem(TOKEN_KEY, t);
    url.searchParams.delete("token");
    history.replaceState(null, "", url.toString());
  }
}

export function token(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export class ApiError extends Error {
  status: number;
  details: ValidationDetail[];
  constructor(status: number, message: string, details: ValidationDetail[] = []) {
    super(message);
    this.status = status;
    this.details = details;
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = {};
  const t = token();
  if (t) headers.Authorization = `Bearer ${t}`;
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const res = await fetch(path, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
    credentials: "same-origin",
  });
  const text = await res.text();
  const data = text ? JSON.parse(text) : null;
  if (!res.ok) {
    throw new ApiError(res.status, data?.detail ?? res.statusText, data?.details ?? []);
  }
  return data as T;
}

export const api = {
  get: <T>(path: string) => request<T>("GET", path),
  post: <T>(path: string, body?: unknown) => request<T>("POST", path, body ?? {}),
  put: <T>(path: string, body: unknown) => request<T>("PUT", path, body),
  patch: <T>(path: string, body: unknown) => request<T>("PATCH", path, body),
  del: <T>(path: string) => request<T>("DELETE", path),
};

export function wsUrl(path: string): string {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  const t = token();
  const sep = path.includes("?") ? "&" : "?";
  return `${proto}//${location.host}${path}${t ? `${sep}token=${encodeURIComponent(t)}` : ""}`;
}

export function enc(s: string): string {
  return encodeURIComponent(s);
}
