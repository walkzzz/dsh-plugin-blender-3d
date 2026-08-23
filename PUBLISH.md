# Publishing dsh-plugin-blender-3d to the DeepSeek Harness plugin market

> **Status (2025-08-24):** source pushed to
> [`walkzzz/dsh-plugin-blender-3d`](https://github.com/walkzzz/dsh-plugin-blender-3d)
> (10 commits), release `v0.1.0` with prebuilt tarball uploaded, and catalog PR
> [#2933](https://github.com/awesome-dsh-plugin/awesome-dsh-plugin/pull/2933)
> opened against `awesome-dsh-plugin/awesome-dsh-plugin` (entry:
> `data/plugins/walkzzz__dsh-plugin-blender-3d.yml`). CI: README regen ✅,
> 10-commit bar ✅; only the repo-age gate (≥1 day) is pending — retrigger CI
> (push any commit to the fork branch, or close/reopen) after ~24h, or a
> maintainer can merge. npm publish was skipped (no npm credentials); installs
> use the GitHub tarball, which still skips the build-approval step.

The market (`dshmarket`) browses the curated catalog at
**https://awesome-dsh-plugin.com/plugins.json**, which is backed by the GitHub
repo [`awesome-dsh-plugin/awesome-dsh-plugin`](https://github.com/awesome-dsh-plugin/awesome-dsh-plugin).
Installs are restricted to sources in that catalog, so "publishing" = **(1) ship
an npm package** + **(2) add one catalog entry**. The market + site pick it up
automatically, usually within a day.

## 0. Prerequisites

- Node ≥ 18, npm account, the headless runtime deps on the target machine:
  ```sh
  pip install numpy trimesh manifold3d
  ```

## 1. Publish the npm package

```sh
cd dsh-plugin-blender-3d
npm login
npm publish --access public
```

`package.json` already has `"publishConfig": { "access": "public" }`,
`"type": "module"`, `main`, `exports`, `files`, and the `dsh.bundle.patch`
pointer — i.e. it is a valid public Cordis plugin.

> Verify locally first: `npm pack` and inspect the tarball contains
> `lib/index.js`, `lib/bridge-client.js`, `lib/runtime/**`, `skill/**`,
> `cordis.patch.yml`.

## 2. Add the catalog entry (the actual "marketplace" listing)

Open a PR against
[`awesome-dsh-plugin/awesome-dsh-plugin`](https://github.com/awesome-dsh-plugin/awesome-dsh-plugin)
appending **one** object to the plugins list. The ready-to-use object is in
[`registry-entry.json`](./registry-entry.json):

```json
{
  "name": "dsh-plugin-blender-3d",
  "owner": "LOOYIABC",
  "url": "https://gitcode.com/LOOYIABC/blender",
  "category": "tools",
  "description": { "en": "…", "zh": "…" },
  "npm": "dsh-plugin-blender-3d",
  "stars": 0,
  "install": "dsh plugin --profile web add dsh-plugin-blender-3d",
  "added": "2025-08-24"
}
```

Schema (from `dshmarket`'s `RegistryPlugin`): `name`, `owner`, `url`, `category`,
`description` (`{en, zh}`), `npm`, `stars`, `install`, `added` (YYYY-MM-DD).
Maintainers may reclassify `category` — pick the closest existing one.

## 3. Install (works the moment the npm package exists, even before the PR merges)

```sh
dsh plugin --profile web add dsh-plugin-blender-3d
```

Restart `dsh web`, open **Settings → Plugin Market**, search `blender`.

## Local / pre-publish install (this repo)

Install straight from the local directory (pnpm supports path deps) to try it
before publishing:

```sh
dsh plugin --profile web add /workspace/dsh-plugin-blender-3d
```

## Optional: drive real Blender instead of the headless runtime

1. Install the add-on: Blender → Edit → Preferences → Add-ons → Install →
   pick `lib/runtime/blender_ai_bridge/__init__.py`. Enable it.
2. In its N-panel set host `127.0.0.1`, port e.g. `13090`, click **Start**.
3. `export AIB_PORT=13090` before `dsh web`. Same tool, full Blender.
