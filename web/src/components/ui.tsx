import { X } from "lucide-react";
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { useStore } from "../lib/store";

export function Toasts() {
  const toasts = useStore((s) => s.toasts);
  const dismiss = useStore((s) => s.dismiss);
  return (
    <div className="toasts">
      {toasts.map((t) => (
        <div key={t.id} className={`toast ${t.level}`}>
          <div className="grow">{t.msg}</div>
          <button className="icon-btn sm" onClick={() => dismiss(t.id)}>
            <X size={14} />
          </button>
        </div>
      ))}
    </div>
  );
}

/** Horizontal (or vertical) resizable split; sizes persisted per id. */
export function Split({
  id,
  left,
  right,
  initial = 0.55,
  vertical = false,
  min = 0.15,
}: {
  id: string;
  left: ReactNode;
  right: ReactNode;
  initial?: number;
  vertical?: boolean;
  min?: number;
}) {
  const key = `netsim.split.${id}`;
  const [ratio, setRatio] = useState(() => Number(localStorage.getItem(key)) || initial);
  const box = useRef<HTMLDivElement>(null);
  const [drag, setDrag] = useState(false);

  const onDown = useCallback(
    (e: React.PointerEvent) => {
      e.preventDefault();
      setDrag(true);
      const rect = box.current!.getBoundingClientRect();
      const move = (ev: PointerEvent) => {
        const r = vertical ? (ev.clientY - rect.top) / rect.height : (ev.clientX - rect.left) / rect.width;
        setRatio(Math.min(1 - min, Math.max(min, r)));
      };
      const up = () => {
        setDrag(false);
        window.removeEventListener("pointermove", move);
        window.removeEventListener("pointerup", up);
      };
      window.addEventListener("pointermove", move);
      window.addEventListener("pointerup", up);
    },
    [vertical, min],
  );

  useEffect(() => {
    if (!drag) localStorage.setItem(key, String(ratio));
  }, [drag, ratio, key]);

  return (
    <div ref={box} className="split" style={{ flexDirection: vertical ? "column" : "row" }}>
      <div className="pane" style={{ flex: `${ratio} 1 0` }}>
        {left}
      </div>
      <div className={`splitter ${vertical ? "h" : ""} ${drag ? "dragging" : ""}`} onPointerDown={onDown} />
      <div className="pane" style={{ flex: `${1 - ratio} 1 0` }}>
        {right}
      </div>
    </div>
  );
}

/** Imperative prompt dialog: `await prompt({title})` resolves to the value or null. */
export function prompt(opts: { title: string; label?: string; value?: string; placeholder?: string; ok?: string }) {
  return new Promise<string | null>((resolve) => {
    const host = document.createElement("div");
    document.body.appendChild(host);
    const root = createRoot(host);
    const done = (v: string | null) => {
      root.unmount();
      host.remove();
      resolve(v);
    };
    root.render(<PromptDialog {...opts} done={done} />);
  });
}

function PromptDialog({
  title,
  label,
  value = "",
  placeholder,
  ok = "OK",
  done,
}: {
  title: string;
  label?: string;
  value?: string;
  placeholder?: string;
  ok?: string;
  done: (v: string | null) => void;
}) {
  const [v, setV] = useState(value);
  return (
    <div className="modal-back" onMouseDown={() => done(null)}>
      <form
        className="modal"
        onMouseDown={(e) => e.stopPropagation()}
        onSubmit={(e) => {
          e.preventDefault();
          done(v.trim() || null);
        }}
      >
        <h3>{title}</h3>
        <label className="field">
          {label && <span>{label}</span>}
          <input autoFocus value={v} placeholder={placeholder} onChange={(e) => setV(e.target.value)}
            onKeyDown={(e) => e.key === "Escape" && done(null)} />
        </label>
        <div className="row" style={{ justifyContent: "flex-end" }}>
          <button type="button" className="btn" onClick={() => done(null)}>
            Cancel
          </button>
          <button type="submit" className="btn primary">
            {ok}
          </button>
        </div>
      </form>
    </div>
  );
}

export function Empty({ icon, title, children }: { icon?: ReactNode; title: string; children?: ReactNode }) {
  return (
    <div className="empty">
      <div className="col" style={{ alignItems: "center", gap: 8 }}>
        {icon}
        <div style={{ fontWeight: 600, color: "var(--text)" }}>{title}</div>
        {children && <div style={{ maxWidth: 420 }}>{children}</div>}
      </div>
    </div>
  );
}

/** Re-render periodically (for relative times, countdowns). */
export function useTick(ms = 1000) {
  const [, set] = useState(0);
  useEffect(() => {
    const h = setInterval(() => set((x) => x + 1), ms);
    return () => clearInterval(h);
  }, [ms]);
}
