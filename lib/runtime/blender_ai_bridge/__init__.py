# SPDX-License-Identifier: GPL-3.0-or-later
bl_info = {
    "name": "AI Bridge (DeepSeek Harness)",
    "author": "DeepSeek Harness",
    "version": (1, 0, 0),
    "blender": (3, 0, 0),
    "location": "View3D > Sidebar (N) > AI Bridge",
    "description": "Expose a JSON command API over HTTP so an external agent "
                   "(DeepSeek harness) can drive 3D modeling: primitives, "
                   "booleans, transforms, materials, modifiers, scene, "
                   "selection, export, scripts.",
    "category": "Interface",
}

import bpy
from bpy.props import StringProperty, IntProperty, BoolProperty
from bpy.types import Operator, Panel, AddonPreferences

from . import server as _server

# Single shared server instance for the add-on.
_SERVER = _server.BridgeHTTPServer()


# --------------------------------------------------------------------------- #
# operators
# --------------------------------------------------------------------------- #
class AIB_OT_start(Operator):
    bl_idname = "aib.start"
    bl_label = "Start AI Bridge"
    bl_description = "Start the HTTP bridge server on the configured port"

    def execute(self, context):
        prefs = _prefs(context)
        _SERVER.host = prefs.host
        _SERVER.port = prefs.port
        try:
            _SERVER.start()
        except OSError as exc:
            self.report({'ERROR'}, "Failed to start: %s" % exc)
            return {'CANCELLED'}
        _server.ensure_timer()
        prefs.running = True
        self.report({'INFO'}, "AI Bridge on %s" % _SERVER.url())
        return {'FINISHED'}


class AIB_OT_stop(Operator):
    bl_idname = "aib.stop"
    bl_label = "Stop AI Bridge"
    bl_description = "Stop the HTTP bridge server"

    def execute(self, context):
        _SERVER.stop()
        _prefs(context).running = False
        self.report({'INFO'}, "AI Bridge stopped")
        return {'FINISHED'}


class AIB_OT_status(Operator):
    bl_idname = "aib.status"
    bl_label = "Bridge Status"
    bl_description = "Print bridge status to the info area"

    def execute(self, context):
        state = "running at %s" % _SERVER.url() if _SERVER.running else "stopped"
        self.report({'INFO'}, "AI Bridge %s" % state)
        return {'FINISHED'}


# --------------------------------------------------------------------------- #
# panel
# --------------------------------------------------------------------------- #
class AIB_PT_panel(Panel):
    bl_label = "AI Bridge"
    bl_idname = "AIB_PT_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "AI Bridge"

    def draw(self, context):
        prefs = _prefs(context)
        layout = self.layout
        col = layout.column(align=True)
        if _SERVER.running:
            col.label(text="Running: %s" % _SERVER.url(), icon='CHECKMARK')
            col.operator("aib.stop", icon='PAUSE')
        else:
            col.label(text="Stopped", icon='X')
            col.operator("aib.start", icon='PLAY')
        col.separator()
        col.prop(prefs, "host")
        col.prop(prefs, "port")
        col.separator()
        box = col.box()
        box.label(text="Commands:", icon='SCRIPT')
        for line in ("create_shape, boolean, transform",
                     "duplicate, delete, material, modifier",
                     "scene, selection, export, import",
                     "camera, light, render, script"):
            box.label(text=line)


# --------------------------------------------------------------------------- #
# preferences
# --------------------------------------------------------------------------- #
def _prefs(context):
    return context.preferences.addons[__name__].preferences


class AIBPreferences(AddonPreferences):
    bl_idname = __name__

    host: StringProperty(name="Host", default="127.0.0.1")
    port: IntProperty(name="Port", default=13080, min=1, max=65535)
    running: BoolProperty(name="Running", default=False)

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "host")
        layout.prop(self, "port")
        if _SERVER.running:
            layout.operator("aib.stop")
        else:
            layout.operator("aib.start")


# --------------------------------------------------------------------------- #
# registration
# --------------------------------------------------------------------------- #
_classes = (AIB_OT_start, AIB_OT_stop, AIB_OT_status, AIB_PT_panel, AIBPreferences)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)
    # Auto-start in headless mode so `blender --background --python` just works.
    try:
        if bpy.app.background:
            bpy.app.handlers.event_timer  # noqa: B018 - probe attribute
            _SERVER.host = "127.0.0.1"
            _SERVER.port = int(__import__("os").environ.get("AIB_PORT", "13080"))
            _SERVER.start()
            _server.ensure_timer()
    except Exception:
        pass


def unregister():
    _SERVER.stop()
    for cls in reversed(_classes):
        try:
            bpy.utils.unregister_class(cls)
        except Exception:
            pass


if __name__ == "__main__":
    register()
