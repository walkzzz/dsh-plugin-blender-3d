// SPDX-License-Identifier: MIT
/**
 * Environment bootstrap for dsh-plugin-blender-3d — detect, install,
 * configure, verify. Node stdlib only; ESM. No function throws: failures are
 * returned as `{ ok: false, detail }` so a tool caller always gets a report.
 *
 * Two runtimes:
 *  - headless: pure-Python `headless_bridge.py` (trimesh + manifold3d). Needs
 *    `pip install numpy trimesh manifold3d`. This is the default and needs no
 *    Blender. `ensurePythonDeps` creates an isolated venv for it.
 *  - bpy add-on: needs a real Blender install. `ensureBlender` can fetch a
 *    portable Blender (linux tar.xz) on request; mac/win return instructions.
 */

import { execFile } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import os from "node:os";
import https from "node:https";
import { createWriteStream } from "node:fs";

const DATA_DIR = process.env.DSH_HOME
  ? path.join(process.env.DSH_HOME, ".blender-plugin")
  : path.join(os.homedir(), ".blender-plugin");
const CONFIG_PATH = path.join(DATA_DIR, "config.json");

export function getConfigPath() { return CONFIG_PATH; }

/** Read persisted config (pythonPath/blenderPath/...). Never throws. */
export function readConfig() {
  try {
    const raw = fs.readFileSync(CONFIG_PATH, "utf8");
    return JSON.parse(raw);
  } catch { return null; }
}

function mkdirp(p) { try { fs.mkdirSync(p, { recursive: true }); } catch { /* ignore */ } }

/** Run a command, return {ok, stdout, stderr, code}. Never throws. */
function run(cmd, args = [], { timeout = 10000 } = {}) {
  return new Promise((resolve) => {
    execFile(cmd, args, { timeout, maxBuffer: 4 * 1024 * 1024 }, (err, stdout, stderr) => {
      if (err) resolve({ ok: false, stdout: stdout || "", stderr: (stderr || "") + (err.message || ""), code: err.code ?? null });
      else resolve({ ok: true, stdout: stdout || "", stderr: stderr || "", code: 0 });
    });
  });
}

function venvBin(venvPath) {
  return process.platform === "win32"
    ? { python: path.join(venvPath, "Scripts", "python.exe"), pip: path.join(venvPath, "Scripts", "pip.exe") }
    : { python: path.join(venvPath, "bin", "python"), pip: path.join(venvPath, "bin", "pip") };
}

// ---------------------------------------------------------------------------
// detect
// ---------------------------------------------------------------------------

async function findPython() {
  for (const c of ["python3", "python"]) {
    const v = await run(c, ["--version"]);
    if (v.ok) {
      const version = (v.stdout || v.stderr).trim().split(/\s+/)[1] || null;
      return { path: c, version, ok: true };
    }
  }
  return null;
}

async function checkDeps(python) {
  const out = { numpy: false, trimesh: false, manifold3d: false };
  if (!python) return out;
  for (const mod of ["numpy", "trimesh", "manifold3d"]) {
    const r = await run(python, ["-c", `import ${mod}`], { timeout: 8000 });
    out[mod] = r.ok;
  }
  return out;
}

async function findBlender() {
  const cands = [];
  if (process.platform === "win32") {
    cands.push("blender.exe");
    for (const d of ["C:\\Program Files\\Blender Foundation", "C:\\Program Files (x86)\\Blender Foundation"]) {
      try {
        for (const sub of fs.readdirSync(d)) cands.push(path.join(d, sub, "blender.exe"));
      } catch { /* ignore */ }
    }
  } else if (process.platform === "darwin") {
    cands.push("/Applications/Blender.app/Contents/MacOS/blender", "blender");
  } else {
    cands.push("blender", "/usr/bin/blender", "/usr/local/bin/blender", "/opt/blender/blender",
      path.join(os.homedir(), ".local", "share", "blender", "blender"));
  }
  for (const c of cands) {
    const v = await run(c, ["--version"], { timeout: 8000 });
    if (v.ok) {
      const version = (v.stdout || v.stderr).trim().split(/\s+/)[1] || null;
      return { path: c, version, ok: true };
    }
  }
  return null;
}

/** Detect the environment. Never throws. */
export async function detect() {
  const python = await findPython();
  const deps = await checkDeps(python ? python.path : null);
  const blender = await findBlender();
  return {
    platform: process.platform,
    arch: process.arch,
    python,
    deps,
    blender,
    inBlender: false, // node context; the bpy add-on sets this inside Blender
  };
}

// ---------------------------------------------------------------------------
// install
// ---------------------------------------------------------------------------

/** Create/refresh a venv with numpy/trimesh/manifold3d. Returns venv python path. */
export async function ensurePythonDeps({ python, force = false } = {}) {
  mkdirp(DATA_DIR);
  const basePython = python || (await findPython())?.path || "python3";
  const venvPath = path.join(DATA_DIR, "venv");
  const bin = venvBin(venvPath);
  const actions = [];

  const needCreate = force || !fs.existsSync(bin.python);
  if (needCreate) {
    let r = await run(basePython, ["-m", "venv", venvPath], { timeout: 30000 });
    if (!r.ok) r = await run(basePython, ["-m", "venv", "--system-site-packages", venvPath], { timeout: 30000 });
    actions.push(`venv create: ${r.ok ? "ok" : "failed"}`);
    if (!r.ok) return { ok: false, venvPath, pythonPath: bin.python, detail: r.stderr.slice(0, 500), actions };
  }

  const deps = ["numpy>=1.26", "trimesh>=4.0", "manifold3d>=2.2"];
  const inst = await run(bin.python, ["-m", "pip", "install", "-q", "--disable-pip-version-check", ...deps], { timeout: 180000 });
  actions.push(`pip install: ${inst.ok ? "ok" : "failed"}`);
  if (!inst.ok) return { ok: false, venvPath, pythonPath: bin.python, detail: inst.stderr.slice(0, 500), actions };

  return { ok: true, venvPath, pythonPath: bin.python, actions };
}

function download(url, dest) {
  return new Promise((resolve) => {
    const file = createWriteStream(dest);
    const req = https.get(url, (res) => {
      if (res.statusCode >= 300 && res.statusCode < 400 && res.headers.location) {
        file.close(); try { fs.unlinkSync(dest); } catch { /* ignore */ }
        return download(res.headers.location, dest).then(resolve);
      }
      if (res.statusCode !== 200) { file.close(); return resolve({ ok: false, detail: `HTTP ${res.statusCode}` }); }
      res.pipe(file);
      file.on("finish", () => file.close(() => resolve({ ok: true })));
    });
    req.on("error", (e) => { file.close(); resolve({ ok: false, detail: e.message }); });
    req.setTimeout(300000, () => req.destroy(new Error("download timeout")));
  });
}

/** Fetch + extract a portable Blender (linux x64 tar.xz). mac/win: instructions only. */
export async function ensureBlender({ version = "4.2.0", dir } = {}) {
  mkdirp(DATA_DIR);
  const destDir = dir || path.join(DATA_DIR, "blender");
  const major = version.split(".").slice(0, 2).join(".");
  const base = `https://download.blender.org/release/Blender${major}/`;

  if (process.platform === "linux" && process.arch === "x64") {
    const url = `${base}blender-${version}-linux-x64.tar.xz`;
    mkdirp(destDir);
    const archive = path.join(DATA_DIR, `blender-${version}.tar.xz`);
    const dl = await download(url, archive);
    if (!dl.ok) return { ok: false, detail: `download failed: ${dl.detail}`, url };
    const ex = await run("tar", ["-xJf", archive, "-C", destDir], { timeout: 120000 });
    if (!ex.ok) return { ok: false, detail: `extract failed: ${ex.stderr.slice(0, 300)}`, url };
    // find the blender binary
    let blenderPath = null;
    const walk = (d, depth = 0) => {
      if (depth > 3 || blenderPath) return;
      try {
        for (const e of fs.readdirSync(d)) {
          const p = path.join(d, e);
          if (e === "blender" && fs.statSync(p).isFile() && fs.statSync(p).mode & 0o111) { blenderPath = p; return; }
          try { if (fs.statSync(p).isDirectory()) walk(p, depth + 1); } catch { /* ignore */ }
        }
      } catch { /* ignore */ }
    };
    walk(destDir);
    return { ok: !!blenderPath, blenderPath, dir: destDir, url };
  }

  // mac / win / other: don't half-extract; give the user the URL + instructions.
  const url = process.platform === "darwin"
    ? `${base}blender-${version}-macos-arm64.dmg`
    : process.platform === "win32"
      ? `${base}blender-${version}-windows-x64.zip`
      : `${base}blender-${version}-linux-x64.tar.xz`;
  return {
    ok: false,
    detail: `auto-extract unsupported on ${process.platform}/${process.arch}; download and install manually, then run blender_setup again to detect it.`,
    url,
  };
}

// ---------------------------------------------------------------------------
// verify
// ---------------------------------------------------------------------------

/** Smoke-test the runtimes. Never throws. */
export async function verify({ pythonPath, blenderPath } = {}) {
  const out = { trimesh: null, blender: null };

  if (pythonPath) {
    const r = await run(pythonPath, ["-c",
      "import trimesh,numpy,manifold3d; m=trimesh.creation.box(extents=[1,2,3]); assert len(m.faces)==12; print('trimesh_ok', len(m.vertices))"],
      { timeout: 20000 });
    out.trimesh = { ok: r.ok, detail: (r.ok ? r.stdout.trim() : r.stderr.slice(0, 300)) };
  }

  if (blenderPath) {
    const r = await run(blenderPath, ["--background", "--python-expr",
      "import bpy; print('blender_ok', bpy.app.version_string)"], { timeout: 30000 });
    out.blender = { ok: r.ok, detail: (r.ok ? r.stdout.trim().split("\n").pop() : r.stderr.slice(0, 300)) };
  }

  return out;
}

// ---------------------------------------------------------------------------
// orchestrate
// ---------------------------------------------------------------------------

/** Full bootstrap: detect → install deps → (optional) blender → verify → persist config. */
export async function setup({ installBlender = false, blenderVersion, force = false } = {}) {
  const actions = [];
  const detected = await detect();
  actions.push(`detected python=${detected.python ? detected.python.path : "none"}, blender=${detected.blender ? detected.blender.path : "none"}`);

  if (!detected.python) {
    return { ok: false, stage: "detect", detected, actions, config: null, verify: null,
      detail: "no python3/python found; install Python 3.10+ first." };
  }

  // 1) Python deps in an isolated venv.
  const deps = await ensurePythonDeps({ python: detected.python.path, force });
  actions.push(...(deps.actions || []));
  let pythonPath = deps.ok ? deps.pythonPath : detected.python.path;
  if (!deps.ok) actions.push(`deps install failed: ${deps.detail}`);

  // 2) Optional Blender.
  let blenderPath = detected.blender ? detected.blender.path : null;
  if (installBlender && !blenderPath) {
    const b = await ensureBlender({ version: blenderVersion || "4.2.0" });
    actions.push(`blender install: ${b.ok ? "ok" : "skipped — " + (b.detail || "")}`);
    if (b.ok && b.blenderPath) blenderPath = b.blenderPath;
  }

  // 3) Verify.
  const verifyResult = await verify({ pythonPath, blenderPath });

  // 4) Persist config.
  const config = {
    pythonPath,
    blenderPath: blenderPath || undefined,
    venvPath: deps.ok ? deps.venvPath : undefined,
    updatedAt: new Date().toISOString(),
    versions: {
      python: detected.python?.version,
      blender: detected.blender?.version,
    },
  };
  try { mkdirp(DATA_DIR); fs.writeFileSync(CONFIG_PATH, JSON.stringify(config, null, 2)); } catch { /* best-effort */ }

  const ok = !!(verifyResult.trimesh && verifyResult.trimesh.ok);
  return { ok, stage: "done", detected, actions, config, verify: verifyResult };
}
