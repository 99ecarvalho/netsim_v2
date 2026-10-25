import { useEffect, useRef } from "react";
import uPlot from "uplot";
import "uplot/dist/uPlot.min.css";
import { useStore } from "../lib/store";

export interface SeriesDef {
  label: string;
  color: string; // css variable name, e.g. "--up"
  fill?: boolean;
  dash?: boolean;
  points?: boolean;
  scale?: string;
  width?: number;
}

interface Props {
  series: SeriesDef[];
  data: (number | null)[][]; // [xs(seconds), ...ys]
  height?: number;
  yFormat?: (v: number) => string;
  y2Format?: (v: number) => string;
  yMin?: number;
  window?: number; // seconds of x range to show, follows the newest point
}

function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || "#888";
}

function alpha(color: string, a: number): string {
  if (color.startsWith("#") && color.length === 7) {
    const n = parseInt(color.slice(1), 16);
    return `rgba(${n >> 16}, ${(n >> 8) & 255}, ${n & 255}, ${a})`;
  }
  return color;
}

export default function Chart({ series, data, height = 160, yFormat, y2Format, yMin = 0, window }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const plot = useRef<uPlot | null>(null);
  const dark = useStore((s) => s.dark);
  const dataRef = useRef(data);
  dataRef.current = data;

  useEffect(() => {
    const el = ref.current!;
    const grid = cssVar("--grid");
    const muted = cssVar("--muted");
    const axis = (fmt?: (v: number) => string, side = 3, scale = "y"): uPlot.Axis => ({
      scale,
      side,
      stroke: muted,
      grid: { stroke: grid, width: 1, show: side === 3 },
      ticks: { show: false },
      size: 52,
      font: "11px system-ui",
      values: fmt ? (_u, vals) => vals.map((v) => (v == null ? "" : fmt(v))) : undefined,
    });
    const hasY2 = series.some((s) => s.scale === "y2");
    const opts: uPlot.Options = {
      width: el.clientWidth || 400,
      height,
      legend: { show: true, live: true },
      cursor: { drag: { x: false, y: false }, points: { size: 6 } },
      scales: {
        x: { time: true },
        y: { range: (_u, _min, max) => [yMin, max > yMin ? max * 1.1 : yMin + 1] },
        ...(hasY2 ? { y2: { range: (_u, _min, max) => [0, max > 0 ? max * 1.1 : 1] } } : {}),
      },
      axes: [
        { stroke: muted, grid: { stroke: grid, width: 1 }, ticks: { show: false }, font: "11px system-ui", size: 28 },
        axis(yFormat),
        ...(hasY2 ? [axis(y2Format, 1, "y2")] : []),
      ],
      series: [
        { label: "time", value: (_u, v) => (v == null ? "" : new Date(v * 1000).toLocaleTimeString()) },
        ...series.map((s) => {
          const c = cssVar(s.color);
          return {
            label: s.label,
            stroke: c,
            width: s.width ?? 1.6,
            fill: s.fill ? alpha(c, dark ? 0.14 : 0.1) : undefined,
            dash: s.dash ? [4, 4] : undefined,
            scale: s.scale ?? "y",
            spanGaps: false,
            points: { show: s.points ?? false, size: 4, fill: c },
            value: (_u: uPlot, v: number | null) =>
              v == null ? "-" : (s.scale === "y2" ? y2Format : yFormat)?.(v) ?? String(v),
          };
        }),
      ],
    };
    const u = new uPlot(opts, dataRef.current as uPlot.AlignedData, el);
    plot.current = u;
    const ro = new ResizeObserver(() => u.setSize({ width: el.clientWidth, height }));
    ro.observe(el);
    return () => {
      ro.disconnect();
      u.destroy();
      plot.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dark, height, series.map((s) => s.label + s.color).join()]);

  useEffect(() => {
    const u = plot.current;
    if (!u) return;
    u.setData(data as uPlot.AlignedData, !window);
    if (window && data[0].length) {
      const max = data[0][data[0].length - 1] as number;
      u.setScale("x", { min: max - window, max });
    }
  }, [data, window]);

  return <div ref={ref} className="chart" />;
}
