// SPDX-License-Identifier: MIT
/**
 * dsh-plugin-blender-3d — DeepSeek Harness Cordis plugin.
 *
 * Registers a model-facing `blender_model` tool that drives 3D modeling
 * through a JSON bridge (create primitives, boolean CSG, transform, material,
 * modifier, scene, selection, export STL/OBJ/PLY/GLTF, run scripts). The
 * bridge is the bundled pure-Python `headless_bridge.py` (trimesh + manifold3d,
 * no Blender required) and is started on demand and kept alive for the session.
 *
 * The same protocol is spoken by the bundled Blender add-on
 * (`runtime/blender_ai_bridge/`), so pointing the tool at a running Blender
 * (AIB_PORT) upgrades to full Blender power with no code change.
 *
 * OOM-safe: heavy geometry never enters the Node/JSON channel — exports return
 * a `file_path` the agent reads from disk with its own file tools.
 */

import { call, ensureBridge, stopBridge } from "./bridge-client.js";
import { setup as runSetup } from "./setup.js";
import { fileURLToPath } from "node:url";
import path from "node:path";
import fs from "node:fs";

export const name = "dsh-plugin-blender-3d";
export const inject = ["tools", "effect"];

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const BUNDLED_SKILL = path.join(__dirname, "..", "skill", "blender-3d-modeling", "SKILL.md");

/** Hand-rolled JSON Schema (zero deps), mirroring the skill-search preset. */
function schema(spec) {
  const properties = {};
  const required = [];
  for (const [k, m] of Object.entries(spec || {})) {
    properties[k] = { type: m.type, ...(m.description ? { description: m.description } : {}) };
    if (m.required) required.push(k);
  }
  return { type: "object", properties, required, additionalProperties: false };
}

/** Idempotently materialize the bundled skill into the user skill root. */
function installSkill() {
  try {
    const dshHome = process.env.DSH_HOME;
    if (!dshHome) return;
    const destDir = path.join(dshHome, "skills", "blender-3d-modeling");
    const dest = path.join(destDir, "SKILL.md");
    if (fs.existsSync(dest)) return;            // don't clobber an existing/newer skill
    fs.mkdirSync(destDir, { recursive: true });
    fs.copyFileSync(BUNDLED_SKILL, dest);
  } catch { /* best-effort; never break boot */ }
}

export function apply(ctx, config = {}) {
  installSkill();

  const opts = {
    host: config.host || process.env.AIB_HOST || "127.0.0.1",
    port: config.port || process.env.AIB_PORT || 13082,
    python: config.python || process.env.AIB_PYTHON || "python3",
    startupMs: config.startupMs || 20000,
    timeoutMs: config.timeoutMs || 60000,
  };

  ctx.tools.register({
    name: "blender_model",
    description:
      "Drive 3D modeling through the Blender AI bridge. Pass a `command` and " +
      "`args`; the bridge runs it and returns a JSON result. Commands: " +
      "create_shape, boolean, transform, duplicate, delete, rename, material, " +
      "modifier, mesh_edit, scene, selection, scene_clear, camera, light, render, " +
      "export, import, script, command, document. Exports return a file_path " +
      "(read the model from disk with your file tools — geometry is never inlined). " +
      "First call auto-starts a headless Python bridge (trimesh+manifold3d); set " +
      "AIB_PORT to drive a real Blender running the bundled add-on instead.",
    parameters: schema({
      command: { type: "string", required: true, description: "bridge command, e.g. create_shape / boolean / export / scene" },
      args: { type: "object", description: "command arguments object" },
    }),
    output: {
      schema: { type: "object", additionalProperties: false, properties: { text: { type: "string" } }, required: ["text"] },
      render: (_a, v) => [{ type: "text", text: v.text }],
    },
    async execute(args, _exec) {
      const command = args?.command;
      const commandArgs = args?.args || {};
      if (!command) throw new Error("blender_model requires a `command` string");
      const result = await call(command, commandArgs, opts);
      return { text: JSON.stringify(result, null, 2) };
    },
  });

  // Optional: a thin `blender_scene` convenience tool for quick inspection.
  ctx.tools.register({
    name: "blender_scene",
    description:
      "Return a lightweight summary of the current 3D scene (object names, types, " +
      "locations, vertex/face counts). No mesh data — OOM-safe. Equivalent to " +
      "blender_model {command:'scene'}.",
    parameters: schema({}),
    output: {
      schema: { type: "object", additionalProperties: false, properties: { text: { type: "string" } }, required: ["text"] },
      render: (_a, v) => [{ type: "text", text: v.text }],
    },
    async execute(_a, _exec) {
      const result = await call("scene", {}, opts);
      return { text: JSON.stringify(result, null, 2) };
    },
  });

  // Auto environment setup: detect → install deps (venv) → optional Blender → verify.
  ctx.tools.register({
    name: "blender_setup",
    description:
      "Auto-detect, install, configure, and verify the Blender 3D modeling " +
      "environment. Creates an isolated Python venv with numpy/trimesh/manifold3d " +
      "(no Blender needed for headless modeling/export). Set install_blender=true " +
      "to also fetch a portable Blender (linux x64; other platforms get instructions). " +
      "Returns a JSON report with detected paths, actions taken, and verification results.",
    parameters: schema({
      install_blender: { type: "boolean", description: "also download+install Blender (linux x64; default false)" },
      blender_version: { type: "string", description: "Blender version to fetch, e.g. 4.2.0" },
      force: { type: "boolean", description: "recreate the venv even if it exists" },
    }),
    output: {
      schema: { type: "object", additionalProperties: false, properties: { text: { type: "string" } }, required: ["text"] },
      render: (_a, v) => [{ type: "text", text: v.text }],
    },
    async execute(args, _exec) {
      try {
        const report = await runSetup({
          installBlender: args?.install_blender === true,
          blenderVersion: args?.blender_version,
          force: args?.force === true,
        });
        return { text: JSON.stringify(report, null, 2) };
      } catch (e) {
        return { text: JSON.stringify({ ok: false, error: e.message }, null, 2) };
      }
    },
  });

  // Stop the spawned bridge when the harness tears down this composition.
  try {
    ctx.effect(function* () { yield async () => stopBridge(); }, "blender-3d-bridge");
  } catch { /* effect service unavailable — ignore */ }
}

export { ensureBridge };
