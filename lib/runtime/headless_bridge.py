#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Headless 3D-modeling bridge — pure Python, no Blender required.

Implements the SAME JSON command API as the `blender_ai_bridge` Blender add-on,
so the DeepSeek harness driver talks to either runtime identically. Uses
`trimesh` + `manifold3d` for real CSG booleans and STL/OBJ/PLY/GLTF export.

Run:
    python3 /workspace/blender_harness/headless_bridge.py --port 13080
    GET http://127.0.0.1:13080/health

Protocol (identical to the Blender add-on):
    POST /  {"id":1,"command":"create_shape","args":{"type":"box","dx":2}}
        ->  {"id":1,"result":{"ok":true,...}} | {"id":1,"error":"..."}
"""

import argparse
import base64
import json
import math
import os
import sys
import tempfile
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np
import trimesh
import trimesh.transformations as tf

try:
    import manifold3d  # noqa: F401
    _HAVE_MANIFOLD = True
except Exception:
    _HAVE_MANIFOLD = False

# Prefer the manifold backend for booleans when available.
_BOOL_ENGINE = "manifold" if _HAVE_MANIFOLD else None


# --------------------------------------------------------------------------- #
# scene model
# --------------------------------------------------------------------------- #
class Object:
    def __init__(self, name, mesh, kind="mesh"):
        self.name = name
        self.mesh = mesh  # trimesh.Trimesh in world space (transforms baked)
        self.kind = kind
        self.material = None  # dict of material props
        self.selected = False

    def summary(self):
        d = {"name": self.name, "type": self.kind,
             "location": list(self.mesh.centroid.astype(float)) if self.mesh is not None else [0, 0, 0],
             "vertices": int(len(self.mesh.vertices)) if self.mesh is not None else 0,
             "polygons": int(len(self.mesh.faces)) if self.mesh is not None else 0,
             "scale": [1.0, 1.0, 1.0],
             "rotation": [0.0, 0.0, 0.0]}
        if self.material:
            d["material"] = self.material
        return d


class Scene:
    def __init__(self):
        self.objects = {}  # name -> Object
        self._counter = 0
        self.tmp_dir = tempfile.gettempdir()
        self.active = None

    def _unique(self, base):
        self._counter += 1
        return "%s.%03d" % (base, self._counter)

    def add(self, name, mesh, kind="mesh"):
        if name is None or name in self.objects:
            name = self._unique(name or kind)
        obj = Object(name, mesh, kind)
        self.objects[name] = obj
        self.active = name
        return obj


# --------------------------------------------------------------------------- #
# command handler
# --------------------------------------------------------------------------- #
class HeadlessHandler:
    def __init__(self):
        self.scene = Scene()

    def dispatch(self, payload):
        cmd = payload.get("command")
        args = payload.get("args") or {k: v for k, v in payload.items()
                                       if k not in ("command", "args", "id")}
        fn = getattr(self, "cmd_" + cmd, None) if cmd else None
        if fn is None:
            return {"ok": False, "error": "unknown command: %r" % cmd}
        try:
            res = fn(args)
            if res is None:
                res = {"ok": True}
            if isinstance(res, dict) and "ok" not in res:
                res = dict(res, ok=True)
            return res
        except Exception as e:
            return {"ok": False, "error": str(e),
                    "trace": traceback.format_exc(limit=6)}

    # ---- helpers ----
    def _vec(self, v, d=(0.0, 0.0, 0.0)):
        if v is None:
            return np.array(d, dtype=float)
        return np.array([float(v[0]), float(v[1]), float(v[2])], dtype=float)

    def _obj(self, name):
        o = self.scene.objects.get(name)
        if o is None:
            raise KeyError("object not found: %r" % name)
        return o

    def _apply_transform(self, mesh, location=None, rotation=None, scale=None,
                         rotation_degrees=False):
        if scale is not None:
            s = self._vec(scale, (1, 1, 1))
            mesh.apply_transform(tf.scale_matrix(s[0], [1, 0, 0]) @
                                 tf.scale_matrix(s[1], [0, 1, 0]) @
                                 tf.scale_matrix(s[2], [0, 0, 1]))
        if rotation is not None:
            r = [float(x) for x in rotation]
            if rotation_degrees:
                r = [math.radians(x) for x in r]
            mat = (tf.rotation_matrix(r[0], [1, 0, 0]) @
                   tf.rotation_matrix(r[1], [0, 1, 0]) @
                   tf.rotation_matrix(r[2], [0, 0, 1]))
            mesh.apply_transform(mat)
        if location is not None:
            mesh.apply_translation(self._vec(location))
        return mesh

    # ---- primitives ----
    def cmd_create_shape(self, a):
        t = a.get("type", "box")
        if t == "box":
            s = a.get("size")
            if s is None:
                dx, dy, dz = a.get("dx", 1), a.get("dy", 1), a.get("dz", 1)
            elif isinstance(s, (list, tuple)):
                dx, dy, dz = float(s[0]), float(s[1]), float(s[2])
            else:
                dx = dy = dz = float(s)
            mesh = trimesh.creation.box(extents=(dx, dy, dz))
        elif t == "sphere":
            mesh = trimesh.creation.uv_sphere(
                radius=a.get("radius", 1.0),
                count=(a.get("segments", 32), a.get("rings", 16)))
        elif t == "ico_sphere":
            mesh = trimesh.creation.icosphere(
                radius=a.get("radius", 1.0),
                subdivisions=a.get("subdivisions", 2))
        elif t == "cylinder":
            mesh = trimesh.creation.cylinder(
                radius=a.get("radius", 1.0),
                height=a.get("height", a.get("depth", 2.0)),
                sections=a.get("vertices", 32))
        elif t == "cone":
            mesh = trimesh.creation.cone(
                radius=a.get("radius", 1.0),
                height=a.get("height", 2.0),
                sections=a.get("vertices", 32))
        elif t == "torus":
            mesh = trimesh.creation.torus(
                major_radius=a.get("major_radius", 1.0),
                minor_radius=a.get("minor_radius", 0.25),
                major_sections=a.get("major_segments", 48),
                minor_sections=a.get("minor_segments", 16))
        elif t == "plane":
            s = a.get("size", 2.0)
            mesh = trimesh.creation.box(extents=(s, s, 0.0))
            # collapse to a single quad plane
            mesh = trimesh.Trimesh(
                vertices=np.array([[-s/2, -s/2, 0], [s/2, -s/2, 0],
                                   [s/2, s/2, 0], [-s/2, s/2, 0]],
                                  dtype=float),
                faces=np.array([[0, 1, 2], [0, 2, 3]]))
        elif t == "grid":
            s = a.get("size", 2.0)
            nx = a.get("x_subdivisions", 10)
            ny = a.get("y_subdivisions", 10)
            xs = np.linspace(-s/2, s/2, nx + 1)
            ys = np.linspace(-s/2, s/2, ny + 1)
            gv, gf = [], []
            for i, x in enumerate(xs):
                for j, y in enumerate(ys):
                    gv.append([x, y, 0.0])
            idx = lambda i, j: i * (ny + 1) + j
            for i in range(nx):
                for j in range(ny):
                    a0, b0 = idx(i, j), idx(i + 1, j)
                    c0, d0 = idx(i + 1, j + 1), idx(i, j + 1)
                    gf.append([a0, b0, c0]); gf.append([a0, c0, d0])
            mesh = trimesh.Trimesh(vertices=np.array(gv, dtype=float),
                                   faces=np.array(gf))
        elif t == "circle":
            r = a.get("radius", 1.0)
            n = a.get("vertices", 32)
            ang = np.linspace(0, 2 * math.pi, n, endpoint=False)
            verts = np.column_stack([r * np.cos(ang), r * np.sin(ang),
                                     np.zeros(n)])
            verts = np.vstack([verts, [0, 0, 0]])
            faces = [[n, i, (i + 1) % n] for i in range(n)]
            mesh = trimesh.Trimesh(vertices=verts, faces=faces)
        elif t == "monkey":
            raise ValueError("Suzanne not available headless; use Blender add-on")
        else:
            raise ValueError("unknown shape type: %r" % t)

        self._apply_transform(mesh, a.get("location"), a.get("rotation"),
                              a.get("scale"), a.get("rotation_degrees", False))
        obj = self.scene.add(a.get("name"), mesh, kind=t)
        return {"ok": True, "name": obj.name, "type": t,
                "location": list(mesh.centroid.astype(float)),
                "vertices": int(len(mesh.vertices))}

    # ---- transforms ----
    def cmd_transform(self, a):
        o = self._obj(a["name"])
        if "location" in a:
            o.mesh.apply_translation(self._vec(a["location"]) - o.mesh.centroid)
        if "scale" in a:
            s = self._vec(a["scale"], (1, 1, 1))
            o.mesh.apply_transform(
                tf.scale_matrix(s[0], [1, 0, 0]) @
                tf.scale_matrix(s[1], [0, 1, 0]) @
                tf.scale_matrix(s[2], [0, 0, 1]))
        if "rotation" in a:
            r = [float(x) for x in a["rotation"]]
            if a.get("rotation_degrees"):
                r = [math.radians(x) for x in r]
            mat = (tf.rotation_matrix(r[0], [1, 0, 0]) @
                   tf.rotation_matrix(r[1], [0, 1, 0]) @
                   tf.rotation_matrix(r[2], [0, 0, 1]))
            o.mesh.apply_transform(mat)
        return {"ok": True, "name": o.name,
                "location": list(o.mesh.centroid.astype(float))}

    def cmd_duplicate(self, a):
        o = self._obj(a["name"])
        count = int(a.get("count", 1))
        off = self._vec(a.get("offset", (0, 0, 0)))
        base = a.get("new_name") or a.get("as") or o.name
        created = []
        for i in range(count):
            m = o.mesh.copy()
            m.apply_translation(off * (i + 1))
            nm = base if count == 1 else "%s.%03d" % (base, i + 1)
            no = self.scene.add(nm, m, kind=o.kind)
            no.material = o.material
            created.append(no.name)
        return {"ok": True, "created": created}

    def cmd_delete(self, a):
        names = a.get("names") or [a["name"]]
        removed = [n for n in names if n in self.scene.objects]
        for n in removed:
            del self.scene.objects[n]
        return {"ok": True, "removed": removed}

    def cmd_rename(self, a):
        o = self._obj(a["name"])
        old = o.name
        del self.scene.objects[old]
        o.name = a["new_name"]
        self.scene.objects[o.name] = o
        return {"ok": True, "name": o.name}

    # ---- boolean ----
    def cmd_boolean(self, a):
        op = a.get("operation", "difference").lower()
        target = self._obj(a["target"])
        tools = [self._obj(n) for n in (a.get("tools") or
                  ([a["tool"]] if "tool" in a else []))]
        if not tools:
            raise ValueError("boolean needs >=1 tool")
        result = target.mesh
        for tool in tools:
            if op == "union":
                result = trimesh.boolean.union([result, tool.mesh], engine=_BOOL_ENGINE)
            elif op == "difference":
                result = trimesh.boolean.difference([result, tool.mesh], engine=_BOOL_ENGINE)
            elif op == "intersect":
                result = trimesh.boolean.intersection([result, tool.mesh], engine=_BOOL_ENGINE)
            else:
                raise ValueError("bad operation: %s" % op)
        if not isinstance(result, trimesh.Trimesh) or len(result.faces) == 0:
            raise ValueError("boolean produced empty mesh")
        name = a.get("name", target.name)
        if a.get("remove_tools", False):
            for tool in tools:
                if tool.name in self.scene.objects and tool.name != target.name:
                    del self.scene.objects[tool.name]
        if not a.get("remove_tools", False) and target.name in self.scene.objects and name == target.name:
            target.mesh = result
            return {"ok": True, "name": name, "operation": op,
                    "vertices": int(len(result.vertices))}
        obj = self.scene.add(name, result, kind="boolean")
        if a.get("remove_tools", False) and target.name in self.scene.objects and target.name != obj.name:
            del self.scene.objects[target.name]
        return {"ok": True, "name": obj.name, "operation": op,
                "vertices": int(len(result.vertices))}

    # ---- material ----
    def cmd_material(self, a):
        action = a.get("action", "assign")
        if action == "list":
            return {"ok": True, "materials": sorted({o.material["name"]
                    for o in self.scene.objects.values() if o.material})}
        if action == "create":
            mat = {"name": a.get("name", "Material"),
                   "color": a.get("color", [0.8, 0.8, 0.8, 1.0]),
                   "metallic": a.get("metallic", 0.0),
                   "roughness": a.get("roughness", 0.5)}
            return {"ok": True, "name": mat["name"], "_mat": mat}
        o = self._obj(a["name"])
        o.material = {"name": a.get("material", "Material"),
                      "color": a.get("color", [0.8, 0.8, 0.8, 1.0]),
                      "metallic": a.get("metallic", 0.0),
                      "roughness": a.get("roughness", 0.5)}
        return {"ok": True, "name": o.name, "material": o.material["name"]}

    # ---- modifiers (subset) ----
    def cmd_modifier(self, a):
        action = a.get("action", "add")
        o = self._obj(a["name"])
        if action == "list":
            return {"ok": True, "modifiers": []}
        if action == "remove":
            return {"ok": True}
        mtype = a.get("type", "").lower()
        p = a.get("params", {}) or {}
        if mtype in ("subsurf", "subdivision", "subdivide"):
            o.mesh = o.mesh.subdivide(iterations=int(p.get("levels", p.get("iterations", 1))))
        elif mtype == "array":
            axis = p.get("axis", "X").upper()
            cnt = int(p.get("count", 2))
            off = float(p.get("offset", 1.0))
            ai = {"X": 0, "Y": 1, "Z": 2}[axis]
            meshes = [o.mesh.copy()]
            for i in range(1, cnt):
                m = o.mesh.copy()
                t = np.zeros(3); t[ai] = off * i
                m.apply_translation(t)
                meshes.append(m)
            o.mesh = trimesh.util.concatenate(meshes)
        elif mtype == "mirror":
            axis = p.get("axis", "X").upper()
            ai = {"X": 0, "Y": 1, "Z": 2}[axis]
            m = o.mesh.copy()
            s = np.eye(4); s[ai, ai] = -1
            m.apply_transform(s)
            o.mesh = trimesh.util.concatenate([o.mesh, m])
        else:
            # bevel/solidify not implemented headless; no-op with note.
            return {"ok": True, "modifier": mtype,
                    "note": "modifier '%s' is a no-op in headless bridge" % mtype}
        return {"ok": True, "modifier": mtype,
                "vertices": int(len(o.mesh.vertices))}

    # ---- mesh edit (subset) ----
    def cmd_mesh_edit(self, a):
        o = self._obj(a["name"])
        action = a["action"]
        if action == "subdivide":
            o.mesh = o.mesh.subdivide(iterations=int(a.get("cuts", 1)))
        elif action == "recalc_normals":
            o.mesh.fix_normals()
        elif action in ("extrude", "inset"):
            return {"ok": True, "note": "%s not implemented headless" % action}
        return {"ok": True, "name": o.name,
                "vertices": int(len(o.mesh.vertices)),
                "polygons": int(len(o.mesh.faces))}

    # ---- scene / selection ----
    def cmd_scene(self, a):
        return {"ok": True,
                "objects": [o.summary() for o in self.scene.objects.values()],
                "object_count": len(self.scene.objects),
                "blender_version": [0, 0, 0],
                "runtime": "headless-python",
                "csg_backend": "manifold3d" if _HAVE_MANIFOLD else "none"}

    def cmd_selection(self, a):
        action = a.get("action", "list")
        if action == "list":
            return {"ok": True, "selected": [n for n, o in self.scene.objects.items() if o.selected]}
        if action == "clear":
            for o in self.scene.objects.values():
                o.selected = False
            return {"ok": True}
        if action == "set":
            for o in self.scene.objects.values():
                o.selected = o.name in (a.get("names") or [])
            return {"ok": True, "selected": a.get("names", [])}
        raise ValueError("bad selection action")

    def cmd_scene_clear(self, a):
        n = len(self.scene.objects)
        self.scene.objects.clear()
        return {"ok": True, "removed": n}

    # ---- camera / light / render (no renderer headless) ----
    def cmd_camera(self, a):
        return {"ok": True, "note": "headless bridge has no renderer; use export"}

    def cmd_light(self, a):
        return {"ok": True, "note": "headless bridge has no renderer; use export"}

    def cmd_render(self, a):
        return {"ok": True, "note": "headless bridge has no renderer; use export to STL/OBJ/PLY/GLTF"}

    # ---- export / import ----
    def cmd_export(self, a):
        fmt = a.get("format", "stl").lower().lstrip(".")
        path = a.get("path") or os.path.join(self.scene.tmp_dir, "aib_export.%s" % fmt)
        names = a.get("names")
        meshes = []
        if names:
            meshes = [self._obj(n).mesh for n in names]
        else:
            meshes = [o.mesh for o in self.scene.objects.values() if o.mesh is not None]
        if not meshes:
            raise ValueError("no meshes to export")
        scene = trimesh.Scene(meshes) if len(meshes) > 1 else meshes[0]
        if fmt in ("gltf", "glb"):
            ext = "glb" if fmt == "glb" else "gltf"
            path = os.path.splitext(path)[0] + "." + ext
            data = scene.export(file_type=ext)
        elif fmt == "fbx":
            raise ValueError("FBX export not supported headless; use STL/OBJ/PLY/GLTF")
        else:
            data = scene.export(file_type=fmt)
        if isinstance(data, (bytes, bytearray)):
            with open(path, "wb") as fh:
                fh.write(data)
        else:
            with open(path, "w") as fh:
                fh.write(data)
        result = {"ok": True, "file_path": path, "format": fmt,
                  "size": os.path.getsize(path),
                  "objects": len(meshes)}
        if a.get("return_data"):
            cap = int(a.get("max_inline_bytes", 2 * 1024 * 1024))
            if os.path.getsize(path) <= cap:
                with open(path, "rb") as fh:
                    result["data_b64"] = base64.b64encode(fh.read()).decode("ascii")
        return result

    def cmd_import(self, a):
        path = a["path"]
        mesh = trimesh.load(path, force="mesh")
        obj = self.scene.add(a.get("name") or os.path.splitext(os.path.basename(path))[0], mesh)
        return {"ok": True, "imported": [obj.name],
                "vertices": int(len(mesh.vertices))}

    # ---- script ----
    def cmd_script(self, a):
        code = a["code"]
        g = {"scene": self.scene, "trimesh": trimesh, "np": np, "numpy": np,
             "math": math, "result": None,
             "objects": self.scene.objects}
        exec(compile(code, "<bridge_script>", "exec"), g)
        res = g.get("result")
        try:
            json.dumps(res)
        except TypeError:
            res = str(res)
        return {"ok": True, "result": res}

    def cmd_command(self, a):
        return {"ok": False, "error": "raw bpy operators only available in the Blender add-on"}

    def cmd_document(self, a):
        action = a.get("action", "info")
        if action == "info":
            return {"ok": True, "name": "headless", "runtime": "headless-python",
                    "objects": len(self.scene.objects),
                    "csg_backend": "manifold3d" if _HAVE_MANIFOLD else "none"}
        if action == "new":
            self.scene.objects.clear()
            return {"ok": True, "action": "new"}
        raise ValueError("bad document action: %s" % action)


HANDLER = HeadlessHandler()


# --------------------------------------------------------------------------- #
# HTTP server
# --------------------------------------------------------------------------- #
class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _send(self, code, obj):
        body = json.dumps(obj, default=_json_default).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204); self._cors(); self.end_headers()

    def do_GET(self):
        if self.path == "/health":
            self._send(200, {"ok": True, "service": "blender-ai-bridge",
                             "runtime": "headless-python",
                             "csg_backend": "manifold3d" if _HAVE_MANIFOLD else "none"})
        elif self.path == "/scene":
            self._send(200, {"id": 0, "result": HANDLER.cmd_scene({})})
        elif self.path in ("/", "/info"):
            self._send(200, {"service": "blender-ai-bridge",
                             "runtime": "headless-python",
                             "endpoints": ["/", "/health", "/scene", "POST /"]})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length <= 0 or length > 64 * 1024 * 1024:
            self._send(400, {"error": "bad content length"}); return
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw)
        except Exception as exc:
            self._send(400, {"error": "invalid json: %s" % exc}); return
        self._send(200, {"id": payload.get("id"),
                         "result": HANDLER.dispatch(payload)})


def _json_default(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    return str(o)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=13080)
    args = ap.parse_args()
    httpd = ThreadingHTTPServer((args.host, args.port), _Handler)
    httpd.daemon_threads = True
    print("[AIB-headless] listening on http://%s:%d (csg=%s)" % (
        args.host, args.port, "manifold3d" if _HAVE_MANIFOLD else "none"), flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        httpd.shutdown()


if __name__ == "__main__":
    main()
