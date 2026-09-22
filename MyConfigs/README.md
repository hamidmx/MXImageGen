# MyConfigs

Saved Image Generator / How to Draw configurations, one `.json` file per config.

## Why files and not the browser

The app caches configs in the browser's `localStorage` so they load instantly, but
that cache is **not** storage:

- it is small — a single config carrying reference images can exhaust it, which is
  the "browser cache won't allow this" error;
- clearing browsing history wipes it.

So the file in this folder is the durable copy. The cache is only a convenience.

## Saving

In the app: **Save Config** / **Update Config** → tick *"Also save `<name>.json` for
MyConfigs"* → the file lands in your Downloads folder → move it here.

The checkbox is ticked automatically whenever the config carries attachments. If the
cache rejects a save, the file is written anyway so nothing is lost.

To dump everything at once: **Configs ▾ → ⬇ Export all**.

## Loading

**Configs ▾ → ⬆ Import from MyConfigs**, then select one or many `.json` files from
this folder. A config whose name already exists is replaced; others are added.

## File format

Self-contained — attachments are embedded as data URLs, so a config file works on
any machine with no missing links and nothing to re-pick.

```json
{
  "app": "HowToDraw Image Generator",
  "kind": "config",
  "version": 1,
  "page": "imagegen",
  "name": "Lions kawaii grayblack",
  "description": "What this config is for.",
  "savedAt": "2026-09-14T10:11:12.000Z",
  "values": {
    "prompt": "...",
    "model": "openai_image_25_sunburst",
    "quality": "2K",
    "aspect": "17:22",
    "variations": "1",
    "mode": "new",
    "refImages": [
      { "name": "style.png", "mime": "image/png", "base64": "...", "dataUrl": "data:image/png;base64,..." }
    ]
  }
}
```

`page` is `imagegen` or `draw`; a file is only imported into the page it came from.

These files can contain image data but never contain API keys — those stay in the
browser's local storage and are never written to disk.
