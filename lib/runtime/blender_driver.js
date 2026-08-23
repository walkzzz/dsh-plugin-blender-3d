#!/usr/bin/env node
/**
 * Blender AI Bridge — harness driver.
 *
 * Talks to the JSON command API exposed by EITHER runtime:
 *   - the `blender_ai_bridge` Blender add-on (bpy, desktop or `--background`)
 *   - the pure-Python `headless_bridge.py` (trimesh + manifold3d, no Blender)
 *
 * Both speak the same protocol:
 *   POST / {"id","command","args"} -> {"id","result"|"error"}
 *
 * OOM-safe export: the bridge writes geometry to disk and returns only
 * `file_path`; this driver reads the file from disk when bytes are needed,
 * never expecting large meshes inline in JSON.
 *
 * Usage as a module:
 *   const { BlenderBridge } = require('./blender_driver.js');
 *   const b = new BlenderBridge('http://127.0.0.1:13082');
 *   await b.createShape({type:'box', dx:2, dy:3, dz:4, name:'mybox'});
 *   const stl = await b.export({format:'stl', path:'/tmp/x.stl', names:['mybox']});
 *   const bytes = await b.readFile(stl.file_path);
 *
 * Usage as a CLI (built-in demo / one-shot commands):
 *   node blender_driver.js --url http://127.0.0.1:13082 demo
 *   node blender_driver.js --url http://127.0.0.1:13082 cmd scene '{}'
 *   node blender_driver.js --url http://127.0.0.1:13082 cmd create_shape '{"type":"sphere","radius":2}'
 */

const http = require('http');
const fs = require('fs');
const path = require('path');
const { URL } = require('url');

class BlenderBridge {
  constructor(url = 'http://127.0.0.1:13080', { timeout = 60000 } = {}) {
    this.base = new URL(url);
    this.timeout = timeout;
    this._id = 0;
  }

  _request(method, pth, body) {
    return new Promise((resolve, reject) => {
      const payload = body == null ? null : Buffer.from(JSON.stringify(body));
      const req = http.request(
        {
          method,
          hostname: this.base.hostname,
          port: this.base.port,
          path: pth,
          headers: payload
            ? { 'Content-Type': 'application/json', 'Content-Length': payload.length }
            : {},
        },
        (res) => {
          const chunks = [];
          res.on('data', (c) => chunks.push(c));
          res.on('end', () => {
            const text = Buffer.concat(chunks).toString('utf8');
            if (res.statusCode >= 400) {
              reject(new Error(`HTTP ${res.statusCode}: ${text.slice(0, 500)}`));
              return;
            }
            if (!text) return resolve(null);
            try { resolve(JSON.parse(text)); } catch (e) { resolve({ raw: text }); }
          });
        },
      );
      req.on('error', reject);
      req.setTimeout(this.timeout, () => req.destroy(new Error('timeout')));
      if (payload) req.write(payload);
      req.end();
    });
  }

  /** Low-level: send one command, return the `result` (throws on error). */
  async send(command, args = {}) {
    const id = ++this._id;
    const resp = await this._request('POST', '/', { id, command, args });
    if (!resp) throw new Error('empty response');
    if (resp.error) throw new Error(resp.error);
    if (resp.result && resp.result.ok === false) {
      throw new Error(resp.result.error || 'command failed');
    }
    return resp.result;
  }

  async health() { return this._request('GET', '/health'); }
  async scene() {
    const r = await this._request('GET', '/scene');
    return r && r.result ? r.result : r;
  }

  // ---- typed helpers (mirror the command catalog) ----
  createShape(args) { return this.send('create_shape', args); }
  boolean(args) { return this.send('boolean', args); }
  transform(args) { return this.send('transform', args); }
  duplicate(args) { return this.send('duplicate', args); }
  delete(args) { return this.send('delete', args); }
  rename(args) { return this.send('rename', args); }
  material(args) { return this.send('material', args); }
  modifier(args) { return this.send('modifier', args); }
  meshEdit(args) { return this.send('mesh_edit', args); }
  selection(args) { return this.send('selection', args); }
  sceneClear(args) { return this.send('scene_clear', args); }
  camera(args) { return this.send('camera', args); }
  light(args) { return this.send('light', args); }
  render(args) { return this.send('render', args); }
  exportModel(args) { return this.send('export', args); }
  importModel(args) { return this.send('import', args); }
  script(args) { return this.send('script', args); }
  command(args) { return this.send('command', args); }
  document(args) { return this.send('document', args); }

  /** Read an exported model file from disk (OOM-safe: bytes never in JSON). */
  readFile(file_path) { return fs.promises.readFile(file_path); }
  readFileB64(file_path) {
    return fs.promises.readFile(file_path).then((b) => b.toString('base64'));
  }

  // ---- convenience: build a named primitive ----
  box(name, dx = 1, dy = 1, dz = 1, location) {
    return this.createShape({ type: 'box', name, dx, dy, dz, location });
  }
  sphere(name, radius = 1, location) {
    return this.createShape({ type: 'sphere', name, radius, location });
  }
  cylinder(name, radius = 1, height = 2, location) {
    return this.createShape({ type: 'cylinder', name, radius, height, location });
  }
  /** Boolean difference: target minus tools, returns result object name. */
  cut(name, target, tool, remove = false) {
    return this.boolean({ name, target, tool, operation: 'difference', remove_tools: remove });
  }
  union(name, target, tool) {
    return this.boolean({ name, target, tool, operation: 'union' });
  }
  intersect(name, target, tool) {
    return this.boolean({ name, target, tool, operation: 'intersect' });
  }
}

// --------------------------------------------------------------------------- //
// CLI
// --------------------------------------------------------------------------- //
async function demo(url) {
  const b = new BlenderBridge(url);
  console.log('health:', await b.health());
  await b.sceneClear({ all: true });
  const box = await b.box('body', 4, 4, 4);
  console.log('created', box.name, box.vertices, 'verts');
  const sph = await b.sphere('hole', 1.5);
  console.log('created', sph.name, sph.vertices, 'verts');
  const cyl = await b.cylinder('slot', 0.8, 6);
  await b.transform({ name: 'slot', location: [0, 0, 0] });
  const carved = await b.cut('carved', 'body', 'hole', true);
  console.log('boolean cut ->', carved.name, carved.vertices, 'verts');
  const cut2 = await b.cut('carved2', 'carved', 'slot', true);
  console.log('boolean cut2 ->', cut2.name, cut2.vertices, 'verts');
  const stl = await b.exportModel({ format: 'stl', path: '/workspace/demo_cli.stl', names: ['carved2'] });
  console.log('export STL ->', stl.file_path, stl.size, 'bytes');
  const obj = await b.exportModel({ format: 'obj', path: '/workspace/demo_cli.obj', names: ['carved2'] });
  console.log('export OBJ ->', obj.file_path, obj.size, 'bytes');
  const sc = await b.scene();
  console.log('scene objects:', sc.objects.map((o) => `${o.name}[${o.type}] v=${o.vertices} f=${o.polygons}`));
  console.log('DEMO OK');
}

async function main() {
  const argv = process.argv.slice(2);
  let url = 'http://127.0.0.1:13080';
  const filtered = [];
  for (let i = 0; i < argv.length; i++) {
    if (argv[i] === '--url') { url = argv[++i]; } else filtered.push(argv[i]);
  }
  const [sub, ...rest] = filtered;
  if (sub === 'demo') return demo(url);
  if (sub === 'health') { console.log(await new BlenderBridge(url).health()); return; }
  if (sub === 'scene') { console.log(JSON.stringify(await new BlenderBridge(url).scene(), null, 2)); return; }
  if (sub === 'cmd') {
    const [command, argsJson] = rest;
    const r = await new BlenderBridge(url).send(command, JSON.parse(argsJson || '{}'));
    console.log(JSON.stringify(r, null, 2));
    return;
  }
  console.log('Usage: blender_driver.js --url URL [demo|health|scene|cmd <command> <argsJson>]');
}

if (require.main === module) {
  main().catch((e) => { console.error('Error:', e.message); process.exit(1); });
}

module.exports = { BlenderBridge };
