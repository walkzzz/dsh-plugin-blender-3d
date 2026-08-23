#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Headless launcher: start the AI bridge add-on inside background Blender.

Usage:
    blender --background --python /workspace/blender_ai_bridge/headless_start.py

Optionally pre-load a .blend and set the port:
    AIB_PORT=13080 blender --background myscene.blend --python headless_start.py

The script registers the add-on package, starts the HTTP server, and keeps the
process alive by polling the command queue on the main thread (background
Blender has no event loop pumping timers on its own after the startup script).
"""

import os
import sys
import time
import bpy

# Make the add-on importable when this file is run directly.
HERE = os.path.dirname(os.path.abspath(__file__))
PARENT = os.path.dirname(HERE)
if PARENT not in sys.path:
    sys.path.insert(0, PARENT)

import blender_ai_bridge  # noqa: E402
from blender_ai_bridge import server as _server  # noqa: E402

PORT = int(os.environ.get("AIB_PORT", "13080"))
HOST = os.environ.get("AIB_HOST", "127.0.0.1")


def start():
    try:
        blender_ai_bridge.register()
    except Exception:
        # Already registered or partial; ignore.
        pass
    srv = _server.BridgeHTTPServer(host=HOST, port=PORT)
    srv.start()
    _server.ensure_timer()
    print("[AIB] bridge listening on http://%s:%d (blender %s)" % (
        HOST, PORT, ".".join(str(v) for v in bpy.app.version)), flush=True)
    return srv


def keep_alive(srv):
    # Drain the command queue on the main thread and sleep between ticks.
    # bpy.app.timers may not pump in --background after the script returns, so
    # we poll explicitly.
    try:
        while srv.running:
            _server.drain_once()
            time.sleep(0.01)
    except KeyboardInterrupt:
        srv.stop()


if __name__ == "__main__":
    srv = start()
    keep_alive(srv)
