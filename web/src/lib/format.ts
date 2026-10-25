function short(n: number, digits: number): string {
  // 3 significant digits without exponent notation: 123, 12.3, 1.23
  const d = n >= 100 ? 0 : n >= 10 ? Math.max(0, digits - 2) : Math.max(0, digits - 1);
  return String(Number(n.toFixed(d)));
}

export function fmtBps(v: number | undefined | null, digits = 3): string {
  if (!v) return "0";
  const units: [number, string][] = [
    [1e9, "G"],
    [1e6, "M"],
    [1e3, "k"],
  ];
  for (const [div, u] of units) if (v >= div) return `${short(v / div, digits)}${u}`;
  return v.toFixed(0);
}

export function fmtBytes(v: number | undefined | null): string {
  if (!v) return "0 B";
  const units = ["B", "KiB", "MiB", "GiB"];
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(i ? 1 : 0)} ${units[i]}`;
}

export function fmtTime(t: number): string {
  const d = new Date(t * 1000);
  return d.toLocaleTimeString(undefined, { hour12: false }) + "." + String(d.getMilliseconds()).padStart(3, "0");
}

export function fmtAgo(t: number | null | undefined): string {
  if (!t) return "";
  const s = Math.max(0, Date.now() / 1000 - t);
  if (s < 60) return `${Math.floor(s)}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${Math.floor(s % 60)}s`;
  return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
}

export function debounce<A extends unknown[]>(fn: (...a: A) => void, ms: number) {
  let h: ReturnType<typeof setTimeout> | undefined;
  return (...a: A) => {
    clearTimeout(h);
    h = setTimeout(() => fn(...a), ms);
  };
}
