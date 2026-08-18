# Capturing WebGL / WebGL2 (Chrome ANGLE D3D11 backend)

> **Windows only.** Verified 2026-08-19 with Playwright Chromium (`chrome-1234`,
> window title "Google Chrome for Testing"), RenderDoc 1.41 (`renderdoc.pyd`),
> rdc-cli 0.5.4, AMD RX 9070 XT, capturing a three.js `WebGLRenderer` app
> (Vite dev server). The capture comes out as **D3D11** (ANGLE's default
> backend) — inspect it with the normal `rdc` commands.

## What no longer works (modern Chrome)

- **`--gpu-startup-dialog` is broken on Chrome ≥ ~120** (verified broken on
  Chrome 151 / Chromium 1234): the GPU process no longer pauses on the dialog,
  so the classic "inject into the paused GPU PID" flow (still used by the
  WebGPU recipe for old Canary) never gets a chance — the GPU process
  initializes its device before you can inject.
- **Injecting into an already-running GPU process lands post-device**: RenderDoc
  hooks too late; no captures are possible.
- **Injecting at/before GPU process entry kills it**: the `chrome_elf.dll`
  window where injection must happen is ~0–1 s long; a successful early
  injection crashes the GPU process and Chrome respawns it unhooked.

## Working recipe: `--in-process-gpu` + direct `rdc capture`

Run the GPU stack **inside the browser process** so RenderDoc's normal
launch-time hook covers it from frame 0:

```bash
rdc capture -o C:/Temp/restir.rdc --frame 600 --timeout 90 --keep-alive -- \
  C:/Users/<you>/AppData/Local/ms-playwright/chromium-1234/chrome-win64/chrome.exe \
  --no-sandbox --in-process-gpu --disable-gpu-sandbox --no-first-run \
  --user-data-dir=C:/Temp/chrome-rdc-profile http://localhost:5183
```

Key points:

- `--in-process-gpu` — the essential flag. GPU runs in the main process, so
  `rdc capture -- chrome.exe ...` hooks everything at launch.
- `--no-sandbox --disable-gpu-sandbox` — required, otherwise injection dies.
- **Direct composition stays ENABLED** — do *not* pass
  `--disable-direct-composition` (older guides say to; empirically the capture
  works with default direct composition when using `--in-process-gpu`).
- Fresh `--user-data-dir` per run — avoids profile locks and first-run UI.
- `--keep-alive` — keeps Chrome running after the capture so you can re-capture
  or interact; omit it if you want Chrome to exit right after frame N.
- `--frame N` counts presented frames; the app must keep animating (rAF loop)
  until frame N. Give `--timeout` generous headroom (90 s+ for heavy scenes).

## rdc-cli Windows gotcha: `shlex.join` corrupts backslash paths

`rdc-cli`'s `capture.py` (~line 137) builds the child command line with POSIX
`shlex.join`. On Windows, any argument containing `\` gets wrapped in single
quotes, which `CreateProcess` does not strip — the launch fails or lands in a
garbage directory. **Use forward slashes for ALL paths**: the chrome.exe path,
`--user-data-dir`, and `-o` output path.

## Finding your app's draws in the capture

The capture contains the whole browser frame. To locate the WebGL canvas work:

1. `rdc info --json` → confirm API is **D3D11** and draws > 0.
2. `rdc draws --json` / `rdc stats --json` → look for the draw whose render
   target matches the canvas size (the game FBO, not the browser-window-sized
   swapchain composite).
3. For multi-pass apps (GBuffer / MRT / ping-pong history), the interesting
   pass is the one with multiple outputs — e.g. an MRT draw with
   color + reservoir + surface targets — and its PS bindings show the
   previous-frame history textures (2D) and any volume textures (3D) it reads.
4. Texture contents: `controller.GetTextureData(resourceId,
   rd.Subresource(0,0,0))` via `rdc script yourscript.py --json`; match
   ResourceIds by `int(r.resourceId)` from `controller.GetTextures()`.

## Verification checklist

- `rdc open out.rdc && rdc info --json` → API D3D11, expected resolution,
  nonzero draw count.
- Export the canvas-sized render target (`rdc rt <EID> -o out.png`) and view it
  — you should see the actual game frame, not a black browser window.
