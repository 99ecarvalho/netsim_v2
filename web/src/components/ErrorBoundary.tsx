// SPDX-License-Identifier: LGPL-3.0-or-later
// Copyright (C) 2026 Eduardo Correia
import { Component, type ReactNode } from "react";

interface Props {
  children: ReactNode;
  what?: string;
  resetKey?: unknown; // a change (e.g. new document text) clears the error
}

/** Contains render errors to one panel instead of blanking the whole page. */
export default class ErrorBoundary extends Component<Props, { error: Error | null; key: unknown }> {
  state = { error: null as Error | null, key: undefined as unknown };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  static getDerivedStateFromProps(props: Props, state: { error: Error | null; key: unknown }) {
    if (props.resetKey !== state.key) return { error: null, key: props.resetKey };
    return null;
  }

  componentDidCatch(error: Error) {
    console.error(error);
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="empty">
        <div className="col" style={{ alignItems: "center" }}>
          <b>The {this.props.what ?? "view"} could not be drawn</b>
          <code className="err" style={{ maxWidth: 520, whiteSpace: "pre-wrap" }}>{this.state.error.message}</code>
          <button className="btn small" onClick={() => this.setState({ error: null })}>Try again</button>
        </div>
      </div>
    );
  }
}
