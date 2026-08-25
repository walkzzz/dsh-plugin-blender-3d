# SPDX-License-Identifier: GPL-3.0-or-later
"""Command handlers for the Blender AI Bridge add-on.

All handlers run on Blender's MAIN thread (the HTTP server marshals requests
through a timer queue, see server.py). Each handler takes an args dict and
returns a JSON-serializable dict. Heavy geometry is NEVER returned in the JSON
payload — exports write to disk and return only the file path, and scene
queries return lightweight summaries. This keeps the Node.js harness driver
from blowing the V8 heap on large meshes (OOM fix).
"""

import bpy
import bmesh
import math
import os
import io
import json
import base64
import tempfile
import traceback
from mathutils import Vector, Euler, Matrix

# Blender version tuple, e.g. (4, 2, 0) or (3, 6, 4).
BL_VERSION = bpy.app.version


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _vec(v, default=(0.0, 0.0, 0.0)):
    if v is None:
        return Vector(default)
    if isinstance(v, Vector):
        return v
    return Vector((float(v[0]), float(v[1]), float(v[2])))


def _euler(rot, default=(0.0, 0.0, 0.0)):
    """Accept radians (list/tuple) or degrees when ``rot_is_degrees`` is set."""
    return Euler(_vec(rot, default), 'XYZ')


def _obj(name):
    obj = bpy.data.objects.get(name)
    if obj is None:
        raise KeyError("object not found: %r" % name)
    return obj


def _select_only(names):
    bpy.ops.object.select_all(action='DESELECT')
    selected = []
    for n in names:
        obj = bpy.data.objects.get(n)
        if obj is not None:
            obj.select_set(True)
            selected.append(obj)
    if selected:
        bpy.context.view_layer.objects.active = selected[0]
    return selected


def _apply_active_modifiers(obj, only=None):
    """Apply modifiers on ``obj`` that the bridge added (names start with 'AIB_')."""
    ctx = bpy.context.copy()
    for mod in list(obj.modifiers):
        if mod.name.startswith("AIB_") and (only is None or mod.name == only):
            ctx["modifier"] = mod
            try:
                bpy.ops.object.modifier_apply(ctx, modifier=mod.name)
            except Exception:
                # Fall back: remove if apply fails so we don't leave dangling state.
                obj.modifiers.remove(mod)


def _ensure_active(obj):
    bpy.ops.object.select_all(action='DESELECT')
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


# --------------------------------------------------------------------------- #
# version-tolerant export
# --------------------------------------------------------------------------- #
def _export(fmt, path, selected_only, ascii_out=False):
    """Call the right export operator for the running Blender version.

    4.1+ moved STL/OBJ/PLY/USD to ``bpy.ops.wm.*_export``; 3.x keeps them under
    ``export_mesh`` / ``export_scene``. We try the new API first and fall back.
    """
    fmt = fmt.lower().lstrip(".")
    sel = bool(selected_only)

    def call(op_path, kwargs):
        mod, _, fn = op_path.partition(".")
        op = getattr(getattr(bpy.ops, mod), fn)
        op(**kwargs)

    if fmt == "stl":
        try:
            call("wm.stl_export", {"filepath": path, "export_selected_objects": sel,
                                   "use_ascii": ascii_out})
        except (AttributeError, TypeError):
            call("export_mesh.stl", {"filepath": path, "use_selection": sel,
                                    "ascii": ascii_out})
    elif fmt == "obj":
        try:
            call("wm.obj_export", {"filepath": path, "export_selected_objects": sel})
        except (AttributeError, TypeError):
            call("export_scene.obj", {"filepath": path, "use_selection": sel})
    elif fmt == "ply":
        try:
            call("wm.ply_export", {"filepath": path, "export_selected_objects": sel})
        except (AttributeError, TypeError):
            call("export_mesh.ply", {"filepath": path, "use_selection": sel})
    elif fmt == "fbx":
        call("export_scene.fbx", {"filepath": path, "use_selection": sel})
    elif fmt in ("gltf", "glb"):
        ext = "glb" if fmt == "glb" else "gltf"
        # Force the right extension on disk.
        path = os.path.splitext(path)[0] + "." + ext
        call("export_scene.gltf", {"filepath": path, "use_selection": sel,
                                   "export_format": ('GLB' if ext == 'glb' else 'GLTF_SEPARATE')})
    elif fmt == "blend":
        # Save the whole .blend to path.
        bpy.ops.wm.save_as_mainfile(filepath=path)
    elif fmt == "ply":
        call("export_mesh.ply", {"filepath": path, "use_selection": sel})
    else:
        raise ValueError("unsupported export format: %s" % fmt)
    return path


# --------------------------------------------------------------------------- #
# command dispatcher
# --------------------------------------------------------------------------- #
class BlenderCommandHandler:
    """Stateless command handler. Constructed once per add-on session."""

    def __init__(self, tmp_dir=None):
        self.tmp_dir = tmp_dir or tempfile.gettempdir()

    def dispatch(self, payload):
        cmd = payload.get("command")
        args = payload.get("args", {}) or {}
        # Allow args to be passed at top level for convenience.
        if not args and isinstance(payload, dict):
            args = {k: v for k, v in payload.items() if k not in ("command", "args", "id")}
        try:
            fn = getattr(self, "cmd_" + cmd, None) if cmd else None
            if fn is None:
                raise ValueError("unknown command: %r" % cmd)
            res = fn(args)
            if res is None:
                res = {"ok": True}
            if isinstance(res, dict) and "ok" not in res:
                res = dict(res, ok=True)
            return res
        except Exception as e:
            return {"ok": False, "error": str(e),
                    "trace": traceback.format_exc(limit=6)}


# --------------------------------------------------------------------------- #
# primitives
# --------------------------------------------------------------------------- #
    def cmd_create_shape(self, a):
        t = a.get("type", "box")
        loc = _vec(a.get("location"))
        rot = _euler(a.get("rotation"), (0, 0, 0))
        if a.get("rotation_degrees"):
            rot = Euler((math.radians(v) for v in a["rotation"]), 'XYZ')
        name = a.get("name")

        bpy.ops.object.select_all(action='DESELECT')

        def add(op, **kw):
            op(location=tuple(loc), **kw)

        if t == "box":
            dx, dy, dz = a.get("dx", 1), a.get("dy", 1), a.get("dz", 1)
            s = a.get("size")
            if s is not None:
                dx = dy = dz = s
            add(bpy.ops.mesh.primitive_cube_add, size=1.0)
            obj = bpy.context.active_object
            obj.scale = (dx, dy, dz)
        elif t == "sphere":
            add(bpy.ops.mesh.primitive_uv_sphere_add,
                radius=a.get("radius", 1.0),
                segments=a.get("segments", 32), ring_count=a.get("rings", 16))
            obj = bpy.context.active_object
        elif t == "ico_sphere":
            add(bpy.ops.mesh.primitive_ico_sphere_add,
                radius=a.get("radius", 1.0), subdivisions=a.get("subdivisions", 2))
            obj = bpy.context.active_object
        elif t == "cylinder":
            add(bpy.ops.mesh.primitive_cylinder_add,
                radius=a.get("radius", 1.0), depth=a.get("height", a.get("depth", 2.0)),
                vertices=a.get("vertices", 32))
            obj = bpy.context.active_object
        elif t == "cone":
            add(bpy.ops.mesh.primitive_cone_add,
                radius1=a.get("radius", 1.0), radius2=a.get("radius2", 0.0),
                depth=a.get("height", 2.0), vertices=a.get("vertices", 32))
            obj = bpy.context.active_object
        elif t == "torus":
            add(bpy.ops.mesh.primitive_torus_add,
                major_radius=a.get("major_radius", 1.0),
                minor_radius=a.get("minor_radius", 0.25),
                major_segments=a.get("major_segments", 48),
                minor_segments=a.get("minor_segments", 12))
            obj = bpy.context.active_object
        elif t == "plane":
            add(bpy.ops.mesh.primitive_plane_add, size=a.get("size", 2.0))
            obj = bpy.context.active_object
        elif t == "grid":
            add(bpy.ops.mesh.primitive_grid_add, size=a.get("size", 2.0),
                x_subdivisions=a.get("x_subdivisions", 10),
                y_subdivisions=a.get("y_subdivisions", 10))
            obj = bpy.context.active_object
        elif t == "circle":
            add(bpy.ops.mesh.primitive_circle_add, radius=a.get("radius", 1.0),
                vertices=a.get("vertices", 32))
            obj = bpy.context.active_object
        elif t == "monkey":
            add(bpy.ops.mesh.primitive_monkey_add, size=a.get("size", 2.0))
            obj = bpy.context.active_object
        else:
            raise ValueError("unknown shape type: %r" % t)

        obj.rotation_euler = rot
        if name:
            obj.name = name
        return {"ok": True, "name": obj.name, "type": t,
                "location": tuple(obj.location), "scale": tuple(obj.scale)}


# --------------------------------------------------------------------------- #
# transforms / object ops
# --------------------------------------------------------------------------- #
    def cmd_transform(self, a):
        obj = _obj(a["name"])
        if "location" in a:
            obj.location = _vec(a["location"])
        if "rotation" in a:
            r = a["rotation"]
            obj.rotation_euler = Euler((math.radians(v) for v in r), 'XYZ') \
                if a.get("rotation_degrees") else _euler(r)
        if "scale" in a:
            obj.scale = _vec(a["scale"], (1, 1, 1))
        if "rotation_euler" in a:
            obj.rotation_euler = _euler(a["rotation_euler"])
        return {"ok": True, "name": obj.name,
                "location": tuple(obj.location), "scale": tuple(obj.scale)}

    def cmd_duplicate(self, a):
        obj = _obj(a["name"])
        count = int(a.get("count", 1))
        offset = _vec(a.get("offset", (0, 0, 0)))
        base = a.get("new_name") or a.get("as") or obj.name
        names = []
        for i in range(count):
            _ensure_active(obj)
            bpy.ops.object.duplicate_move(
                TRANSFORM_OT_translate={"value": tuple(offset)})
            dup = bpy.context.active_object
            dup.name = base if count == 1 else "%s.%03d" % (base, i + 1)
            names.append(dup.name)
        return {"ok": True, "created": names}

    def cmd_delete(self, a):
        names = a.get("names") or [a["name"]]
        removed = []
        for n in list(names):
            obj = bpy.data.objects.get(n)
            if obj is not None:
                removed.append(n)
                _ensure_active(obj)
                bpy.ops.object.delete()
        return {"ok": True, "removed": removed}

    def cmd_rename(self, a):
        obj = _obj(a["name"])
        obj.name = a["new_name"]
        return {"ok": True, "name": obj.name}


# --------------------------------------------------------------------------- #
# boolean
# --------------------------------------------------------------------------- #
    def cmd_boolean(self, a):
        op = a.get("operation", "difference").upper()
        if op not in ("UNION", "DIFFERENCE", "INTERSECT"):
            raise ValueError("bad operation: %s" % op)
        target = _obj(a["target"])
        tool_objs = []
        for n in (a.get("tools") or ([a["tool"]] if "tool" in a else [])):
            tool_objs.append(_obj(n))
        if not tool_objs:
            raise ValueError("boolean needs at least one tool object")

        # Boolean modifier only supports one tool at a time; chain them.
        for tool in tool_objs:
            mod = target.modifiers.new(name="AIB_Boolean", type='BOOLEAN')
            mod.operation = op
            mod.object = tool
            if hasattr(mod, 'solver'):
                mod.solver = a.get("solver", 'EXACT')
            if a.get("apply", True):
                _apply_active_modifiers(target, only=mod.name)

        if a.get("remove_tools", False):
            for tool in tool_objs:
                if tool.name in bpy.data.objects:
                    _ensure_active(tool)
                    bpy.ops.object.delete()

        result = {"ok": True, "target": target.name, "operation": op}
        if a.get("name"):
            target.name = a["name"]
            result["name"] = target.name
        return result


# --------------------------------------------------------------------------- #
# modifiers
# --------------------------------------------------------------------------- #
    def cmd_modifier(self, a):
        action = a.get("action", "add")
        obj = _obj(a["name"])
        if action == "add":
            mtype = a["type"].upper()
            mod = obj.modifiers.new(name=a.get("mod_name", "AIB_" + a["type"]),
                                    type=mtype)
            for k, v in (a.get("params") or {}).items():
                if hasattr(mod, k):
                    setattr(mod, k, v)
            if a.get("apply", False):
                _apply_active_modifiers(obj, only=mod.name)
            return {"ok": True, "modifier": mod.name, "type": mtype}
        if action == "remove":
            mod = obj.modifiers.get(a["mod_name"])
            if mod:
                obj.modifiers.remove(mod)
            return {"ok": True}
        if action == "list":
            return {"ok": True, "modifiers": [
                {"name": m.name, "type": m.type} for m in obj.modifiers]}
        raise ValueError("bad modifier action: %s" % action)


# --------------------------------------------------------------------------- #
# materials
# --------------------------------------------------------------------------- #
    def cmd_material(self, a):
        action = a.get("action", "assign")
        if action == "list":
            return {"ok": True, "materials": [
                {"name": m.name} for m in bpy.data.materials]}
        if action == "create":
            mat = bpy.data.materials.new(name=a.get("name", "Material"))
            mat.use_nodes = True
            bsdf = mat.node_tree.nodes.get("Principled BSDF")
            if bsdf and "color" in a:
                bsdf.inputs["Base Color"].default_value = _vec4(a["color"])
            if bsdf and "metallic" in a and "Metallic" in bsdf.inputs:
                bsdf.inputs["Metallic"].default_value = float(a["metallic"])
            if bsdf and "roughness" in a and "Roughness" in bsdf.inputs:
                bsdf.inputs["Roughness"].default_value = float(a["roughness"])
            return {"ok": True, "name": mat.name}
        # assign
        obj = _obj(a["name"])
        mat = bpy.data.materials.get(a["material"])
        if mat is None:
            raise KeyError("material not found: %r" % a["material"])
        if obj.data.materials:
            obj.data.materials[0] = mat
        else:
            obj.data.materials.append(mat)
        return {"ok": True, "name": obj.name, "material": mat.name}


# --------------------------------------------------------------------------- #
# scene / selection (lightweight — no mesh data, OOM-safe)
# --------------------------------------------------------------------------- #
    def cmd_scene(self, a):
        objs = []
        for o in bpy.data.objects:
            d = {"name": o.name, "type": o.type,
                 "location": tuple(o.location), "scale": tuple(o.scale),
                 "rotation": tuple(o.rotation_euler)}
            if o.type == 'MESH':
                d["vertices"] = len(o.data.vertices)
                d["polygons"] = len(o.data.polygons)
                d["materials"] = [m.name for m in o.data.materials if m]
            objs.append(d)
        return {"ok": True, "objects": objs,
                "object_count": len(objs),
                "blender_version": list(BL_VERSION)}

    def cmd_selection(self, a):
        action = a.get("action", "list")
        if action == "list":
            return {"ok": True, "selected": [
                o.name for o in bpy.context.selected_objects]}
        if action == "clear":
            bpy.ops.object.select_all(action='DESELECT')
            return {"ok": True}
        if action == "set":
            _select_only(a.get("names", []))
            return {"ok": True, "selected": a.get("names", [])}
        if action == "active":
            o = bpy.context.active_object
            return {"ok": True, "active": o.name if o else None}
        raise ValueError("bad selection action: %s" % action)

    def cmd_scene_clear(self, a):
        keep = set(a.get("keep_types", []))
        removed = []
        for o in list(bpy.data.objects):
            if o.type in keep:
                continue
            if o.type == 'MESH' or a.get("all", False):
                removed.append(o.name)
                bpy.data.objects.remove(o, do_unlink=True)
        return {"ok": True, "removed": removed}


# --------------------------------------------------------------------------- #
# camera / light / render
# --------------------------------------------------------------------------- #
    def cmd_camera(self, a):
        if a.get("action", "add") == "add":
            bpy.ops.object.camera_add(location=tuple(_vec(a.get("location", (7, -7, 5)))))
            cam = bpy.context.active_object
            if a.get("name"):
                cam.name = a["name"]
            if "rotation" in a:
                cam.rotation_euler = _euler(a["rotation"])
            bpy.context.scene.camera = cam
            return {"ok": True, "name": cam.name}
        raise ValueError("bad camera action")

    def cmd_light(self, a):
        ltype = a.get("type", "POINT").upper()
        energy = float(a.get("energy", 1000))
        bpy.ops.object.light_add(type=ltype, location=tuple(_vec(a.get("location", (4, -4, 6)))))
        light = bpy.context.active_object
        light.data.energy = energy
        if a.get("name"):
            light.name = a["name"]
        return {"ok": True, "name": light.name, "type": ltype}

    def cmd_render(self, a):
        scene = bpy.context.scene
        if "engine" in a:
            scene.render.engine = a["engine"]  # e.g. 'CYCLES', 'BLENDER_EEVEE_NEXT'
        if "resolution" in a:
            scene.render.resolution_x, scene.render.resolution_y = a["resolution"]
        if "samples" in a and scene.render.engine == 'CYCLES':
            scene.cycles.samples = int(a["samples"])
        path = a.get("path")
        if not path:
            path = os.path.join(self.tmp_dir, "aib_render.png")
        scene.render.filepath = path
        bpy.ops.render.render(write_still=True)
        return {"ok": True, "file_path": path}


# --------------------------------------------------------------------------- #
# export / import  (OOM-safe: returns file path, not bytes)
# --------------------------------------------------------------------------- #
    def cmd_export(self, a):
        fmt = a.get("format", "stl")
        path = a.get("path")
        if not path:
            ext = fmt.lower().lstrip(".")
            if ext == "glb":
                ext = "glb"
            elif ext in ("gltf",):
                ext = "gltf"
            path = os.path.join(self.tmp_dir, "aib_export.%s" % ext)
        names = a.get("names")
        selected_only = bool(a.get("selected_only", False))
        if names:
            _select_only(names)
            selected_only = True
        path = _export(fmt, path, selected_only, ascii_out=bool(a.get("ascii", False)))
        result = {"ok": True, "file_path": path, "format": fmt,
                  "size": os.path.getsize(path) if os.path.exists(path) else 0}
        # Optionally inline small files as base64 (only when explicitly asked
        # and below a size cap, to avoid the Node OOM we hit before).
        if a.get("return_data") and os.path.exists(path):
            cap = int(a.get("max_inline_bytes", 2 * 1024 * 1024))
            sz = os.path.getsize(path)
            if sz <= cap:
                with open(path, "rb") as fh:
                    result["data_b64"] = base64.b64encode(fh.read()).decode("ascii")
            else:
                result["data_b64"] = None
                result["note"] = "file too large to inline; read from file_path"
        return result

    def cmd_import(self, a):
        path = a["path"]
        fmt = a.get("format") or os.path.splitext(path)[1].lower().lstrip(".")
        if fmt == "obj":
            try:
                bpy.ops.wm.obj_import(filepath=path)
            except (AttributeError, TypeError):
                bpy.ops.import_scene.obj(filepath=path)
        elif fmt == "stl":
            try:
                bpy.ops.wm.stl_import(filepath=path)
            except (AttributeError, TypeError):
                bpy.ops.import_mesh.stl(filepath=path)
        elif fmt == "fbx":
            bpy.ops.import_scene.fbx(filepath=path)
        elif fmt in ("gltf", "glb"):
            bpy.ops.import_scene.gltf(filepath=path)
        else:
            raise ValueError("unsupported import format: %s" % fmt)
        return {"ok": True, "imported": [o.name for o in bpy.context.selected_objects]}


# --------------------------------------------------------------------------- #
# script / operator / document
# --------------------------------------------------------------------------- #
    def cmd_script(self, a):
        code = a["code"]
        g = {"bpy": bpy, "bmesh": bmesh, "math": math,
             "Vector": Vector, "Euler": Euler, "Matrix": Matrix,
             "result": None}
        exec(compile(code, "<bridge_script>", "exec"), g)
        res = g.get("result")
        try:
            json.dumps(res)
        except TypeError:
            res = str(res)
        return {"ok": True, "result": res}

    def cmd_command(self, a):
        op_path = a["op"]
        params = a.get("params", {}) or {}
        mod, _, fn = op_path.partition(".")
        op = getattr(getattr(bpy.ops, mod), fn)
        ret = op(**params)
        return {"ok": True, "op": op_path, "return": str(ret)}

    def cmd_document(self, a):
        action = a.get("action", "info")
        if action == "info":
            return {"ok": True, "name": bpy.data.filepath or "untitled",
                    "blender_version": list(BL_VERSION),
                    "objects": len(bpy.data.objects)}
        if action == "new":
            bpy.ops.wm.read_homefile(use_empty=True)
            return {"ok": True, "action": "new"}
        if action == "save":
            path = a.get("path") or bpy.data.filepath
            if not path:
                path = os.path.join(self.tmp_dir, "aib_scene.blend")
            bpy.ops.wm.save_as_mainfile(filepath=path)
            return {"ok": True, "file_path": path}
        if action == "open":
            bpy.ops.wm.open_mainfile(filepath=a["path"])
            return {"ok": True, "action": "open"}
        raise ValueError("bad document action: %s" % action)


# --------------------------------------------------------------------------- #
# bmesh edit ops
# --------------------------------------------------------------------------- #
    def cmd_mesh_edit(self, a):
        obj = _obj(a["name"])
        action = a["action"]
        _ensure_active(obj)
        bpy.ops.object.mode_set(mode='EDIT')
        bm = bmesh.from_edit_mesh(obj.data)
        try:
            if action == "subdivide":
                bmesh.ops.subdivide_edges(bm, edges=bm.edges[:],
                                          cuts=int(a.get("cuts", 1)),
                                          use_grid_fill=True)
            elif action == "extrude":
                geom = bmesh.ops.extrude_face_region(bm, geom=bm.faces[:])
                if "offset" in a:
                    off = _vec(a["offset"])
                    for v in geom["geom"]:
                        if isinstance(v, bmesh.types.BMVert):
                            v.co += off
            elif action == "inset":
                bmesh.ops.inset_individual(bm, faces=bm.faces[:],
                                           thickness=float(a.get("thickness", 0.1)))
            elif action == "recalc_normals":
                bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
            else:
                raise ValueError("bad mesh_edit action: %s" % action)
        finally:
            bmesh.update_edit_mesh(obj.data)
            bpy.ops.object.mode_set(mode='OBJECT')
        return {"ok": True, "name": obj.name,
                "vertices": len(obj.data.vertices),
                "polygons": len(obj.data.polygons)}


def _vec4(v, default=(0.8, 0.8, 0.8, 1.0)):
    if v is None:
        return default
    if len(v) == 3:
        return (float(v[0]), float(v[1]), float(v[2]), 1.0)
    return tuple(float(x) for x in v)


# Module-level singleton used by the HTTP server.
HANDLER = BlenderCommandHandler()
