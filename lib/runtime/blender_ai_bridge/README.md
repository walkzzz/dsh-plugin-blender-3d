# Blender AI Bridge — DeepSeek Harness 插件

一个 Blender add-on，把 Blender 的 3D 建模能力通过 **HTTP JSON 命令 API** 暴露出来，
让外部 agent（DeepSeek harness）可以驱动建模：创建图元、布尔运算、变换、材质、修改器、
场景查询、选择、导出、运行脚本。

> 设计与工作区里 `chili3d` 的浏览器桥接同源：插件内开服务、harness 发命令、结果回 JSON。
> 关键防 OOM：**导出只回文件路径，不把网格二进制塞 JSON**；harness 再从磁盘读模型。

## 安装到 Blender

1. `Edit > Preferences > Add-ons > Install…`
2. 选择本目录打包后的 `blender_ai_bridge.zip`（把整个 `blender_ai_bridge/` 目录压缩成 zip）。
3. 勾选启用 **AI Bridge (DeepSeek Harness)**。
4. 在 3D 视图侧栏（按 `N`）打开 **AI Bridge** 面板，点 **Start**。
   或设端口：`Preferences > Add-ons > AI Bridge` 里改 Host/Port（默认 `127.0.0.1:13080`）。

## 无头运行（无界面，CI/服务器）

```bash
AIB_PORT=13080 blender --background --python /workspace/blender_ai_bridge/headless_start.py
```

服务起来后 `GET http://127.0.0.1:13080/health` 返回 `{"ok":true,"blender":[4,2,0],...}`。

## 命令协议

`POST /` body：`{"id":1,"command":"create_shape","args":{"type":"box","dx":2,"dy":3,"dz":4}}`
响应：`{"id":1,"result":{"ok":true,"name":"Cube","type":"box",...}}` 或 `{"id":1,"error":"..."}`

| command | args 摘要 |
|---|---|
| `create_shape` | `type` box/sphere/ico_sphere/cylinder/cone/torus/plane/grid/circle/monkey + 尺寸 + `location`/`rotation`/`name` |
| `boolean` | `target`,`tools`/`tool`,`operation` union/difference/intersect,`apply`,`remove_tools`,`solver` |
| `transform` | `name`,`location`,`rotation`,`scale`,`rotation_degrees` |
| `duplicate` / `delete` / `rename` | 对象复制/删除/改名 |
| `material` | `action` create/assign/list + `color`,`metallic`,`roughness` |
| `modifier` | `action` add/remove/list + `type` subsurf/bevel/array/mirror/solidify + `params`,`apply` |
| `mesh_edit` | `name`,`action` subdivide/extrude/inset/recalc_normals |
| `scene` / `selection` / `scene_clear` | 轻量场景/选择查询（不回网格数据） |
| `camera` / `light` / `render` | 相机/灯光/渲染到文件 |
| `export` | `format` stl/obj/ply/fbx/gltf/glb/blend + `path`/`names` → 返回 `file_path` |
| `import` | `format` + `path` |
| `script` / `command` / `document` | 执行 Python / Blender operator / 新建保存打开 |

## 版本兼容

- 导出自动适配 3.x（`export_mesh.stl`/`export_scene.obj`）与 4.1+（`wm.stl_export`/`wm.obj_export`）。
- 布尔修改器 4.0+ 设 `solver`（默认 `EXACT`）。
- `bl_info` 声明 `blender: (3,0,0)`，3.0+ 均可加载。

## 文件

- `__init__.py` — add-on 入口、注册、N 面板、偏好设置、后台模式自启
- `server.py` — HTTP 服务 + 主线程 marshal（bpy.ops 线程不安全，经 `bpy.app.timers` 转回主线程）
- `commands.py` — 命令处理器（bpy/bmesh）
- `headless_start.py` — `blender --background` 启动器
