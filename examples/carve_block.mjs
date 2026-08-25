// Carve a sphere out of a box and export STL. Demonstrates the bridge-client API.
import { call, ensureBridge, stopBridge } from "../lib/bridge-client.js";
import fs from "node:fs";
const opts = { host: "127.0.0.1", port: 13090, startupMs: 25000 };
await ensureBridge(opts);
await call("scene_clear", {}, opts);
await call("create_shape", { type: "box", name: "Block", size: [2, 2, 2] }, opts);
await call("create_shape", { type: "sphere", name: "Cutter", radius: 1, location: [0, 0, 1] }, opts);
await call("boolean", { operation: "difference", name: "Block", cutter: "Cutter", result_name: "Carved" }, opts);
const r = await call("export", { name: "Carved", format: "stl", path: "./carved.stl" }, opts);
console.log("exported:", r.file_path, fs.statSync(r.file_path).size, "bytes");
stopBridge();
