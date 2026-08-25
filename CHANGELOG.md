# Changelog

## 0.2.0 — 2025-08-24
- **Auto environment setup.** New `blender_setup` tool: detect → install →
  configure → verify, with no manual steps.
  - Detects `python3`/`python`, probes `numpy`/`trimesh`/`manifold3d`, and
    finds an installed Blender (common paths + `blender --version`).
  - Creates an isolated venv (`~/.blender-plugin/venv`) and pip-installs
    `numpy>=1.26 trimesh>=4.0 manifold3d>=2.2`. No Blender needed for headless
    modeling/export.
  - Optional `install_blender: true` fetches a portable Blender (linux x64
    `tar.xz`; mac/win get a URL + manual instructions).
  - Smoke-verifies trimesh (box → 12 faces) and Blender (`bpy` import).
  - Persists `~/.blender-plugin/config.json` so the bridge reuses the venv.
- **Bridge self-heal.** `ensureBridge` now reads the config python and, if the
  bridge fails to start because trimesh is missing, creates the deps venv once
  and retries (bounded, never throws).
- New module `lib/setup.js` (Node stdlib only).

## 0.1.1 — 2025-08-24
- Fix `repository`/`homepage` to point at the published GitHub repo so the
  marketplace's npm probe can map this package back to its catalog entry.
- Published to npm as `dsh-plugin-blender-3d@0.1.1` (install:
  `dsh plugin --profile web add dsh-plugin-blender-3d`).

## 0.1.0 — 2025-08-24
- Initial release.
- Cordis plugin registering `blender_model` and `blender_scene` tools.
- Bundled headless runtime (trimesh + manifold3d) and Blender bpy add-on.
- OOM-safe exports (file_path only).
- Contributed `blender-3d-modeling` skill.
