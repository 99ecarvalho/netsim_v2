// SPDX-License-Identifier: LGPL-3.0-or-later
// Copyright (C) 2026 Eduardo Correia
import React from "react";
import { createRoot } from "react-dom/client";
import "@xyflow/react/dist/style.css";
import "@xterm/xterm/css/xterm.css";
import "./styles.css";
import { initToken } from "./lib/api";

initToken();

// imported after the token is captured: the event socket connects on import
const { default: App } = await import("./App");

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
