// SPDX-License-Identifier: LGPL-3.0-or-later
// Copyright (C) 2026 Eduardo Correia
import { autocompletion, type CompletionContext, type CompletionResult } from "@codemirror/autocomplete";
import { yaml } from "@codemirror/lang-yaml";
import { HighlightStyle, syntaxHighlighting } from "@codemirror/language";
import { forceLinting, linter, lintGutter, type Diagnostic } from "@codemirror/lint";
import { EditorView, keymap } from "@codemirror/view";
import { tags as t } from "@lezer/highlight";
import CodeMirror from "@uiw/react-codemirror";
import { useEffect, useMemo, useRef } from "react";
import { useStore } from "../lib/store";

export interface LineDiagnostic {
  line: number;
  message: string;
  severity?: "error" | "warning";
}

interface Props {
  value: string;
  onChange: (v: string) => void;
  diagnostics?: LineDiagnostic[];
  completions?: (ctx: { key: string | null; line: string }) => { label: string; detail?: string }[];
  onSave?: () => void;
  readOnly?: boolean;
}

const highlight = HighlightStyle.define([
  { tag: [t.propertyName, t.definition(t.propertyName)], color: "var(--accent)" },
  { tag: [t.string, t.special(t.string)], color: "var(--ok)" },
  { tag: [t.number, t.bool, t.null], color: "var(--warn)" },
  { tag: t.comment, color: "var(--faint)", fontStyle: "italic" },
  { tag: [t.punctuation, t.separator, t.bracket], color: "var(--muted)" },
  { tag: t.keyword, color: "var(--danger)" },
]);

const baseTheme = EditorView.theme({
  "&": { backgroundColor: "var(--panel)", color: "var(--text)" },
  ".cm-content": { caretColor: "var(--accent)", padding: "8px 0" },
  ".cm-gutters": { backgroundColor: "var(--panel)", color: "var(--faint)", border: "none" },
  ".cm-activeLine": { backgroundColor: "color-mix(in srgb, var(--accent-soft) 45%, transparent)" },
  ".cm-activeLineGutter": { backgroundColor: "transparent", color: "var(--text)" },
  ".cm-selectionBackground, &.cm-focused .cm-selectionBackground, ::selection": {
    backgroundColor: "color-mix(in srgb, var(--accent) 25%, transparent) !important",
  },
  ".cm-cursor": { borderLeftColor: "var(--accent)" },
  ".cm-tooltip": { backgroundColor: "var(--panel)", border: "1px solid var(--border-strong)", borderRadius: "6px" },
  ".cm-tooltip-autocomplete ul li[aria-selected]": { backgroundColor: "var(--accent-soft)", color: "var(--text)" },
  ".cm-lintRange-error": { backgroundImage: "none", textDecoration: "underline wavy var(--danger)" },
  ".cm-diagnostic-error": { borderLeftColor: "var(--danger)" },
  ".cm-panels": { backgroundColor: "var(--panel-2)", color: "var(--text)" },
  ".cm-foldPlaceholder": { backgroundColor: "var(--panel-3)", border: "none", color: "var(--muted)" },
});

export default function CodeEditor({ value, onChange, diagnostics, completions, onSave, readOnly }: Props) {
  const dark = useStore((s) => s.dark);
  const diagRef = useRef(diagnostics);
  diagRef.current = diagnostics;
  const compRef = useRef(completions);
  compRef.current = completions;
  const saveRef = useRef(onSave);
  saveRef.current = onSave;

  const extensions = useMemo(() => {
    const lint = linter(
      (view) => {
        const out: Diagnostic[] = [];
        for (const d of diagRef.current ?? []) {
          if (d.line < 1 || d.line > view.state.doc.lines) continue;
          const line = view.state.doc.line(d.line);
          const from = line.from + (line.text.length - line.text.trimStart().length);
          out.push({ from, to: Math.max(from + 1, line.to), severity: d.severity ?? "error", message: d.message });
        }
        return out;
      },
      { delay: 50 },
    );
    const complete = (ctx: CompletionContext): CompletionResult | null => {
      if (!compRef.current) return null;
      const word = ctx.matchBefore(/[\w.-]*/);
      const line = ctx.state.doc.lineAt(ctx.pos);
      const before = line.text.slice(0, ctx.pos - line.from);
      const km = before.match(/([\w-]+):\s*[\w.-]*$/);
      const opts = compRef.current({ key: km ? km[1] : null, line: before });
      if (!opts.length || (!word && !ctx.explicit)) return null;
      return { from: word ? word.from : ctx.pos, options: opts.map((o) => ({ ...o, type: "constant" })) };
    };
    return [
      yaml(),
      syntaxHighlighting(highlight),
      baseTheme,
      lintGutter(),
      lint,
      autocompletion({ override: [complete], activateOnTyping: true }),
      keymap.of([
        {
          key: "Mod-s",
          preventDefault: true,
          run: () => {
            saveRef.current?.();
            return true;
          },
        },
      ]),
      EditorView.lineWrapping,
    ];
  }, []);

  const viewRef = useRef<EditorView | null>(null);
  useEffect(() => {
    if (viewRef.current) forceLinting(viewRef.current);
  }, [diagnostics]);

  return (
    <CodeMirror
      onCreateEditor={(v) => (viewRef.current = v)}
      value={value}
      onChange={onChange}
      theme={dark ? "dark" : "light"}
      extensions={extensions}
      readOnly={readOnly}
      basicSetup={{ foldGutter: true, highlightActiveLine: true, autocompletion: false, tabSize: 2 }}
      height="100%"
      style={{ height: "100%", overflow: "hidden" }}
    />
  );
}
