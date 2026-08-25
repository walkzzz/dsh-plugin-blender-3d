---
name: blender-3d-modeling
description: Drive Blender 3D modeling from the harness — create primitives, run boolean CSG, transform, material, modifier, scene query, and export STL/OBJ/PLY/GLTF via a JSON bridge. Works with the Blender add-on (bpy) or a pure-Python headless runtime (trimesh+manifold3d).
whenToUse: Use when the task involves creating, combining, transforming, or exporting 3D models/meshes, or driving Blender programmatically from the agent. Start the bridge, then send commands through the Node driver or HTTP.
---

# Blender 3D Modeling (AI Bridge)

This skill lets the harness agent **drive 3D modeling** through a JSON command
bridge. There are two interchangeable runtimes speaking the **same protocol**:

| Runtime | When to use | How to start |
|---|---|---|
| **Blender add-on** (`bpy`) | Real Blender (desktop or `--background`); full power (modifiers, edit mode, render, FBX) | Install `blender_ai_bridge/` in Blender, press **Start** in the N panel; or `blender --background --python blender_ai_bridge/headless_start.py` |
| **Headless Python** | No Blender installed; pure modeling + CSG + export | `python3 blender_harness/headless_bridge.py --port 13082` |

Both expose `POST / {"id","command","args"} -> {"id","result"|"error"}` and
`GET /health`, `GET /scene`. Default port `13080` (add-on) / choose any free port.

## Files (in this workspace)

- `blender_ai_bridge/` — the Blender add-on (`__init__.py`, `server.py`,
  `commands.py`, `headless_start.py`, `README.md`). Install into Blender via
  Edit > Preferences > Add-ons > Install.
- `blender_harness/headless_bridge.py` — pure-Python runtime (trimesh +
  manifold3d). No Blender required.
- `blender_harness/blender_driver.js` — Node.js harness driver (zero deps,
  Node >= 18). Works with either runtime.

## Starting a bridge

```bash
# Headless (works in this container right now):
python3 /workspace/blender_harness/headless_bridge.py --port 13082 &

# Real Blender, headless:
AIB_PORT=13080 blender --background --python /workspace/blender_ai_bridge/headless_start.py

# Real Blender, GUI: install the add-on, open N panel > AI Bridge > Start.
```

Verify: `curl -s http://127.0.0.1:13082/health` → `{"ok":true,...}`.

## Driving modeling from the agent

Prefer the Node driver (typed API, OOM-safe file reads):

```js
const { BlenderBridge } = require('/workspace/blender_harness/blender_driver.js');
const b = new BlenderBridge('http://127.0.0.1:13082');
await b.sceneClear({ all: true });
await b.box('body', 4, 4, 4);
await b.sphere('hole', 1.5);
await b.cut('carved', 'body', 'hole', true);          // boolean difference, remove tool
await b.transform({ name: 'carved', location: [0, 0, 1] });
const stl = await b.exportModel({ format: 'stl', path: '/tmp/out.stl', names: ['carved'] });
const bytes = await b.readFile(stl.file_path);         // read geometry from disk
console.log(await b.scene());
```

Or one-shot via CLI:
```bash
node /workspace/blender_harness/blender_driver.js --url http://127.0.0.1:13082 demo
node /workspace/blender_harness/blender_driver.js --url http://127.0.0.1:13082 cmd create_shape '{"type":"torus","major_radius":2,"minor_radius":0.3,"name":"ring"}'
```

Or raw HTTP:
```bash
curl -X POST http://127.0.0.1:13082/ -H 'Content-Type: application/json' \
  -d '{"id":1,"command":"create_shape","args":{"type":"cylinder","radius":1,"height":3,"name":"cyl"}}'
```

## Command catalog

| command | key args |
|---|---|
| `create_shape` | `type` ∈ box/sphere/ico_sphere/cylinder/cone/torus/plane/grid/circle/monkey + dims + `location`/`rotation`/`scale`/`name` |
| `boolean` | `target`, `tool`/`tools`, `operation` ∈ union/difference/intersect, `apply`, `remove_tools`, `name` |
| `transform` | `name`, `location`, `rotation` (+`rotation_degrees`), `scale` |
| `duplicate` / `delete` / `rename` | `name`, `count`/`offset`, `names`, `new_name` |
| `material` | `action` ∈ create/assign/list + `color`, `metallic`, `roughness` |
| `modifier` | `action` ∈ add/remove/list + `type` ∈ subsurf/bevel/array/mirror/solidify + `params`, `apply` |
| `mesh_edit` | `name`, `action` ∈ subdivide/extrude/inset/recalc_normals |
| `scene` / `selection` / `scene_clear` | lightweight query (no mesh data in JSON) |
| `camera` / `light` / `render` | camera/light/render-to-file (Blender add-on only) |
| `export` | `format` ∈ stl/obj/ply/fbx/gltf/glb/blend + `path`/`names` → returns **`file_path`** |
| `import` | `format` + `path` |
| `script` / `command` / `document` | run Python / Blender operator / new-save-open |

## Critical rules

1. **Never expect mesh bytes in the JSON.** `export` returns `file_path` only.
   Read the file from disk (`b.readFile(res.file_path)`). This avoids Node OOM
   on large meshes.
2. `scene`/`selection` return **lightweight summaries** (name, type, location,
   vert/face counts) — never full geometry.
3. In the Blender add-on, all `bpy.ops` run on the **main thread** (the server
   marshals via `bpy.app.timers`); do not call bpy from a request thread.
4. The headless runtime bakes transforms into the mesh so booleans are correct
   in world space.
5. CSG backend: headless uses `manifold3d` (watertight results). The Blender
   add-on uses the Boolean modifier (`EXACT` solver on 4.0+).

## Version compatibility

- Add-on loads on Blender 3.0+ (`bl_info.blender: (3,0,0)`).
- Export auto-adapts: 4.1+ `wm.stl_export`/`wm.obj_export` vs 3.x
  `export_mesh.stl`/`export_scene.obj`.
- Boolean solver set only on 4.0+.

## Quick verification (run in this workspace)

```bash
python3 /workspace/blender_harness/headless_bridge.py --port 13082 &
sleep 1
node /workspace/blender_harness/blender_driver.js --url http://127.0.0.1:13082 demo
# expect: DEMO OK  +  /workspace/demo_cli.stl written
```
