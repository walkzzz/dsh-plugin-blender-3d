// Smoke-test the plugin's tool path: start bridge via bridge-client,
// build a carved shape, export to disk, read it back. Proves blender_model works.
import { call, ensureBridge, stopBridge } from "./lib/bridge-client.js";
import fs from "node:fs";

const PORT = 13085;
const opts = { host: "127.0.0.1", port: PORT, python: "python3", startupMs: 25000 };

console.log("1) ensureBridge…");
const up = await ensureBridge(opts);
console.log("   bridge up:", JSON.stringify(up));

console.log("2) scene_clear");
await call("scene_clear", {}, opts);

console.log("3) create box");
await call("create_shape", { type: "box", name: "Block", size: [2, 2, 2], location: [0, 0, 0] }, opts);

console.log("4) create sphere");
await call("create_shape", { type: "sphere", name: "Cutter", radius: 1.0, location: [0, 0, 1] }, opts);

console.log("5) boolean difference Block - Cutter");
const bool = await call("boolean", { operation: "difference", name: "Block", cutter: "Cutter", result_name: "Carved" }, opts);
console.log("   boolean result:", JSON.stringify(bool).slice(0, 160));

console.log("6) export STL");
const exp = await call("export", { name: "Carved", format: "stl", path: "/workspace/plugin_smoke_carved.stl" }, opts);
console.log("   export result:", JSON.stringify(exp));

const fp = exp?.file_path || exp?.result?.file_path;
if (fp && fs.existsSync(fp)) {
  console.log("7) file on disk:", fp, "size=", fs.statSync(fp).size, "bytes ✅");
} else {
  throw new Error("export did not produce a readable file_path: " + JSON.stringify(exp));
}

stopBridge();
console.log("SMOKE PASS ✅");
