# dsh-plugin-blender-3d

A [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) plugin that lets the agent **drive 3D modeling** through a JSON bridge — create primitives, run boolean CSG, transform, assign materials/modifiers, query the scene, and export `STL`/`OBJ`/`PLY`/`GLTF`/`GLB`.

It bundles **two interchangeable runtimes** speaking the same protocol:

| Runtime | Needs Blender? | Engine | Use when |
| --- | --- | --- | --- |
| `headless_bridge.py` (default) | ❌ no | `trimesh` + `manifold3d` | headless servers, CI, the harness |
| `blender_ai_bridge/` add-on | ✅ Blender 3.0+ | `bpy` | you want full Blender power |

Point the tool at a running Blender (set `AIB_PORT` to the add-on's port) and the same commands upgrade to real Blender — no code change.

## Install

```sh
dsh plugin --profile web add dsh-plugin-blender-3d
```

Restart `dsh web` (or let HMR pick it up). The plugin registers two model-facing tools:

- **`blender_model`** — `{ command, args }` → JSON result. Commands: `create_shape`, `boolean`, `transform`, `duplicate`, `delete`, `rename`, `material`, `modifier`, `mesh_edit`, `scene`, `selection`, `scene_clear`, `camera`, `light`, `render`, `export`, `import`, `script`, `command`, `document`.
- **`blender_scene`** — lightweight scene summary (names/types/counts, no mesh data).

It also contributes the **`blender-3d-modeling`** skill (auto-installed into `$DSH_HOME/skills` on first load if absent).

## OOM-safe by design

Heavy geometry **never** enters the Node/JSON tool channel. Exports return a `file_path`; the agent reads the model from disk with its own file tools. This is the fix for the Node `Ineffective mark-compacts near heap limit` crash that occurs when full meshes are stuffed into JSON.

## Configuration

| Env / config | Default | Meaning |
| --- | --- | --- |
| `AIB_PORT` | `13082` | bridge port (set to the add-on port to drive real Blender) |
| `AIB_HOST` | `127.0.0.1` | bridge host |
| `AIB_PYTHON` | `python3` | interpreter for the headless bridge |

## Headless runtime deps

```sh
pip install numpy trimesh manifold3d
```

## Publish to the marketplace

See [`PUBLISH.md`](./PUBLISH.md). TL;DR: `npm publish` → open a PR to
[`awesome-dsh-plugin/awesome-dsh-plugin`](https://github.com/awesome-dsh-plugin/awesome-dsh-plugin)
adding [`registry-entry.json`](./registry-entry.json) to the list.

## License

MIT

## Example

```sh
node examples/carve_block.mjs   # box minus sphere -> carved.stl
```
