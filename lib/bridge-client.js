// SPDX-License-Identifier: MIT
/**
 * Tiny HTTP client + on-demand headless-bridge lifecycle for the
 * dsh-plugin-blender-3d tool. No external deps — Node stdlib only.
 *
 * The bridge is the pure-Python `headless_bridge.py` (trimesh + manifold3d).
 * We keep Node thin: only lightweight command JSON crosses the wire; heavy
 * geometry stays in Python and is written to disk (OOM-safe, see SKILL.md).
 */

import { spawn, execFile } from "node:child_process";
import http from "node:http";
import { fileURLToPath } from "node:url";
import path from "node:path";
import fs from "node:fs";
import os from "node:os";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const BRIDGE_PATH = path.join(__dirname, "runtime", "headless_bridge.py");

// Persisted setup config (written by lib/setup.js). Read here directly to keep
// setup.js an optional import and avoid a circular dependency.
const _DATA_DIR = process.env.DSH_HOME
  ? path.join(process.env.DSH_HOME, ".blender-plugin")
  : path.join(os.homedir(), ".blender-plugin");
const _CONFIG_PATH = path.join(_DATA_DIR, "config.json");

/** Return the configured python binary path, or null. Never throws. */
function readConfigPython() {
  try {
    const cfg = JSON.parse(fs.readFileSync(_CONFIG_PATH, "utf8"));
    return cfg && typeof cfg.pythonPath === "string" ? cfg.pythonPath : null;
  } catch { return null; }
}

/** Quick `python -c "import trimesh"` probe. Never throws. */
function hasTrimesh(python) {
  return new Promise((resolve) => {
    execFile(python, ["-c", "import trimesh"], { timeout: 8000 }, (err) => resolve(!err));
  });
}

// Module-level handle so we don't double-spawn within one harness process.
let _child = null;

function healthCheck(host, port, timeoutMs = 2000) {
  return new Promise((resolve) => {
    const req = http.request(
      { method: "GET", hostname: host, port, path: "/health" },
      (res) => {
        const chunks = [];
        res.on("data", (c) => chunks.push(c));
        res.on("end", () => {
          try { resolve(JSON.parse(Buffer.concat(chunks).toString("utf8"))); }
          catch { resolve(null); }
        });
      },
    );
    req.on("error", () => resolve(null));
    req.setTimeout(timeoutMs, () => req.destroy());
    req.end();
  });
}

async function tryStart(python, host, port, startupMs) {
  // Already up?
  const up = await healthCheck(host, port);
  if (up && up.ok) return { host, port, spawned: false, health: up };

  // Spawn the headless bridge detached.
  if (!_child || _child.exitCode !== null) {
    _child = spawn(python, [BRIDGE_PATH, "--host", host, "--port", String(port)], {
      detached: true,
      stdio: "ignore",
      env: { ...process.env },
    });
    _child.on("error", () => { _child = null; });
    try { _child.unref(); } catch { /* ignore */ }
  }

  // Poll for readiness.
  const deadline = Date.now() + (startupMs || 20000);
  while (Date.now() < deadline) {
    await new Promise((r) => setTimeout(r, 300));
    const h = await healthCheck(host, port);
    if (h && h.ok) return { host, port, spawned: true, health: h };
  }
  return null; // did not come up
}

export async function ensureBridge(opts = {}) {
  const host = opts.host || "127.0.0.1";
  const port = Number(opts.port || process.env.AIB_PORT || 13082);
  const startupMs = opts.startupMs || 20000;
  let python = opts.python || readConfigPython() || process.env.AIB_PYTHON || "python3";

  let r = await tryStart(python, host, port, startupMs);
  if (r) return r;

  // Bounded auto-setup: if the bridge failed to start and trimesh is missing in
  // the chosen python, create the deps venv once and retry with that python.
  if (opts.autoSetup !== false) {
    const ok = await hasTrimesh(python);
    if (!ok) {
      try {
        const setup = await import("./setup.js");
        const deps = await setup.ensurePythonDeps({ python, force: false });
        if (deps.ok && deps.pythonPath) {
          python = deps.pythonPath;
          r = await tryStart(python, host, port, startupMs);
          if (r) return r;
        }
      } catch { /* best-effort; fall through to error */ }
    }
  }

  throw new Error(`blender bridge did not come up on http://${host}:${port} (python=${python})`);
}

export function sendCommand({ host, port, command, args = {}, id = 1, timeoutMs = 60000 }) {
  return new Promise((resolve, reject) => {
    const body = Buffer.from(JSON.stringify({ id, command, args }));
    const req = http.request(
      {
        method: "POST", hostname: host, port, path: "/",
        headers: { "Content-Type": "application/json", "Content-Length": body.length },
      },
      (res) => {
        const chunks = [];
        res.on("data", (c) => chunks.push(c));
        res.on("end", () => {
          const text = Buffer.concat(chunks).toString("utf8");
          if (res.statusCode >= 400) return reject(new Error(`HTTP ${res.statusCode}: ${text.slice(0, 300)}`));
          try { resolve(JSON.parse(text)); } catch { resolve({ raw: text }); }
        });
      },
    );
    req.on("error", reject);
    req.setTimeout(timeoutMs, () => req.destroy(new Error("bridge timeout")));
    req.write(body);
    req.end();
  });
}

export async function call(command, args, opts = {}) {
  const { host, port } = await ensureBridge(opts);
  const resp = await sendCommand({ host, port, command, args, timeoutMs: opts.timeoutMs });
  if (resp && resp.error) throw new Error(resp.error);
  if (resp && resp.result && resp.result.ok === false) {
    throw new Error(resp.result.error || "command failed");
  }
  return resp && resp.result;
}

export function stopBridge() {
  if (_child && _child.exitCode === null) { try { _child.kill(); } catch { /* ignore */ } }
  _child = null;
}
