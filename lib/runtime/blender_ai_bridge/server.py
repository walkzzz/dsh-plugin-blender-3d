# SPDX-License-Identifier: GPL-3.0-or-later
"""HTTP bridge server for the Blender AI Bridge add-on.

Blender's bpy/bpy.ops API is **not thread-safe**: every operator and most data
access must happen on the main thread. The HTTP server therefore runs in a
background thread but never touches bpy directly. Each incoming request is
pushed onto a queue; a ``bpy.app.timers`` callback (registered on the main
thread when the server starts) drains the queue, runs the command through
``commands.HANDLER`` on the main thread, and fulfils a per-request event so the
HTTP thread can write the JSON response.

Protocol (mirrors the chili3d bridge):
    POST /  {"id": <int>, "command": "...", "args": {...}}
        ->  {"id": <int>, "result": {...}} | {"id": <int>, "error": "..."}
    GET  /health  -> {"ok": true, "blender": [...]}
    GET  /scene   -> runs the `scene` command and returns its result
    GET  /        -> service info
"""

import json
import threading
import queue
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import bpy

from . import commands

# Polling interval for the main-thread timer (seconds).
_TIMER_INTERVAL = 0.005

# Bounded queue so a flood of requests can't exhaust memory.
_pending = queue.Queue(maxsize=1024)


class _Task:
    __slots__ = ("payload", "event", "response")

    def __init__(self, payload):
        self.payload = payload
        self.event = threading.Event()
        self.response = None


def _drain_queue():
    """Main-thread timer callback: run all queued commands, then reschedule."""
    drained = 0
    while drained < 64:  # cap work per tick so Blender stays responsive
        try:
            task = _pending.get_nowait()
        except queue.Empty:
            break
        try:
            result = commands.HANDLER.dispatch(task.payload)
            task.response = {"id": task.payload.get("id"),
                             "result": result}
        except Exception as exc:  # pragma: no cover - defensive
            task.response = {"id": task.payload.get("id"),
                             "error": str(exc)}
        task.event.set()
        drained += 1
    return _TIMER_INTERVAL  # reschedule; return None to stop.


def ensure_timer():
    """Register the main-thread drain timer (idempotent)."""
    try:
        if not bpy.app.timers.is_registered(_drain_queue):
            bpy.app.timers.register(_drain_queue, first_interval=_TIMER_INTERVAL)
    except Exception:
        # In some headless contexts timers may not be available; the queue is
        # still drained by `drain_once()` polled from the launcher instead.
        pass


def drain_once(max_tasks=64):
    """Synchronous drain for headless launchers without a timer loop."""
    drained = 0
    while drained < max_tasks:
        try:
            task = _pending.get_nowait()
        except queue.Empty:
            return drained
        try:
            result = commands.HANDLER.dispatch(task.payload)
            task.response = {"id": task.payload.get("id"), "result": result}
        except Exception as exc:
            task.response = {"id": task.payload.get("id"), "error": str(exc)}
        task.event.set()
        drained += 1
    return drained


class BridgeHTTPServer:
    """Owns the ThreadingHTTPServer lifecycle."""

    def __init__(self, host="127.0.0.1", port=13080, timeout=60.0):
        self.host = host
        self.port = port
        self.timeout = timeout
        self._httpd = None
        self._thread = None

    @property
    def running(self):
        return self._httpd is not None

    def start(self):
        if self.running:
            return
        ensure_timer()
        server = ThreadingHTTPServer((self.host, self.port), _Handler)
        server.daemon_threads = True
        server.timeout = 1
        self._httpd = server
        self._thread = threading.Thread(
            target=server.serve_forever, name="AIB-HTTP", daemon=True)
        self._thread.start()

    def stop(self):
        if not self.running:
            return
        self._httpd.shutdown()
        self._httpd.server_close()
        self._httpd = None
        self._thread = None

    def url(self):
        return "http://%s:%d" % (self.host, self.port)


class _Handler(BaseHTTPRequestHandler):
    # Quiet logging.
    def log_message(self, *args):
        pass

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods",
                         "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _send(self, code, obj):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        if self.path == "/health":
            self._send(200, {"ok": True,
                             "blender": list(bpy.app.version),
                             "service": "blender-ai-bridge"})
        elif self.path == "/scene":
            self._send(200, self._run({"id": 0, "command": "scene", "args": {}}))
        elif self.path in ("/", "/info"):
            self._send(200, {"service": "blender-ai-bridge",
                             "blender": list(bpy.app.version),
                             "endpoints": ["/", "/health", "/scene", "POST /"]})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length <= 0 or length > 64 * 1024 * 1024:
            self._send(400, {"error": "bad content length"})
            return
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw)
        except Exception as exc:
            self._send(400, {"error": "invalid json: %s" % exc})
            return
        self._send(200, self._run(payload))

    def _run(self, payload):
        task = _Task(payload)
        _pending.put(task, block=True, timeout=30)
        if not task.event.wait(timeout=self.server.owner_timeout):
            return {"id": payload.get("id"),
                    "error": "timeout waiting for main thread"}
        return task.response


# Stash the per-request timeout on the server so the handler can read it.
def _patch():
    orig_init = ThreadingHTTPServer.__init__

    def init(self, addr, handler, *a, **k):
        orig_init(self, addr, handler, *a, **k)
        self.owner_timeout = 60.0

    ThreadingHTTPServer.__init__ = init


_patch()
