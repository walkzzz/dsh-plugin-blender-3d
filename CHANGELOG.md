# Changelog

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
