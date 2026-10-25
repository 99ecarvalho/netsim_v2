// SPDX-License-Identifier: LGPL-3.0-or-later
// Copyright (C) 2026 Eduardo Correia
// Writes the licenses of every production dependency bundled into the web UI.
import { execFileSync } from "node:child_process";
import { existsSync, readdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";

const tree = JSON.parse(execFileSync("npm", ["ls", "--omit=dev", "--all", "--json", "--long"], { encoding: "utf8" }));
const seen = new Map();
(function walk(deps) {
  for (const [name, d] of Object.entries(deps ?? {})) {
    if (!d.path || seen.has(`${name}@${d.version}`)) continue;
    seen.set(`${name}@${d.version}`, { name, version: d.version, path: d.path });
    walk(d.dependencies);
  }
})(tree.dependencies);

const out = ["Third-party software bundled in the netsim web UI", "=".repeat(48), ""];
const summary = {};
for (const { name, version, path } of [...seen.values()].sort((a, b) => a.name.localeCompare(b.name))) {
  const pkg = JSON.parse(readFileSync(join(path, "package.json"), "utf8"));
  const license = typeof pkg.license === "string" ? pkg.license : pkg.license?.type ?? "UNKNOWN";
  summary[license] = (summary[license] ?? 0) + 1;
  const file = existsSync(path) ? readdirSync(path).find((f) => /^(licen[cs]e|copying)/i.test(f)) : undefined;
  out.push(`${name} ${version} - ${license}`, "-".repeat(60));
  out.push(file ? readFileSync(join(path, file), "utf8").trim() : "(no license file in package)", "", "");
}
out.splice(3, 0, `Packages: ${seen.size}. ` + Object.entries(summary).map(([l, n]) => `${l}: ${n}`).join(", "), "");
writeFileSync("../netsim/web/THIRD_PARTY_LICENSES.txt", out.join("\n"));
console.log(`licenses: ${seen.size} packages (${Object.entries(summary).map(([l, n]) => `${l} ${n}`).join(", ")})`);
