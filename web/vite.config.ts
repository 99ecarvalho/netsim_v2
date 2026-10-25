// SPDX-License-Identifier: LGPL-3.0-or-later
// Copyright (C) 2026 Eduardo Correia
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const backend = process.env.NETSIM_URL ?? "http://127.0.0.1:8077";

export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "../netsim/web",
    target: "es2022",
    emptyOutDir: true,
    chunkSizeWarningLimit: 2000,
  },
  server: {
    proxy: {
      "/api": { target: backend, ws: true, changeOrigin: false },
    },
  },
});
