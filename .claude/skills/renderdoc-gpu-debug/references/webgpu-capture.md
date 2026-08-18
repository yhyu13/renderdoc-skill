# Capturing WebGPU (Chrome D3D12 backend)

> **Regression note (2026-08-19)**: the `--gpu-startup-dialog` pause this recipe
> relies on no longer fires on Chrome ≥ ~120 (verified broken on Chrome 151 /
> Chromium 1234) — the GPU process initializes before you can inject, and early
> injection kills it. For **WebGL/WebGL2** use the `--in-process-gpu` recipe in
> [webgl-chrome-capture.md](webgl-chrome-capture.md) instead. For WebGPU the
> dialog flow below may still work only on the specific old Canary builds it was
> written against.

> **Windows only.** Captures WebGPU by hooking the *browser's* D3D12 backend, not the
> Dawn queue. Written for the three.js `WebGPURenderer` projects in this repo
> (e.g. `12_ddgi`).

RenderDoc has **no native WebGPU support**. You capture WebGPU by injecting into the
Chrome GPU process *before* it initializes its D3D12 device. The result is a plain
**D3D12 capture** — inspect it with the normal `rdc` commands. This file documents the
capture flow and the WebGPU-specific knowledge (Dawn flags, labels, caveats). For the
concrete `12_ddgi` inspection recipe, see `debugging-recipes.md` → "Recipe 7".

## Requirements

- Windows only.
- **Chrome v144+** (at time of writing, only in Canary).
- RenderDoc installed, with **"Enable Process Injection"** enabled
  (`Tools > Settings` → check the box → restart RenderDoc). First launch only.

## Capture flow

1. **Fully exit Chrome** (system-tray icon → "Exit"). Chrome keeps running in the
   background otherwise and won't attach.
2. Launch Chrome from a **command prompt (not PowerShell)** with the injection flag set:
   ```
   set RENDERDOC_HOOK_EGL=0 && "<Chrome Dir>\chrome.exe" --no-sandbox --disable-gpu-sandbox --disable-direct-composition --gpu-startup-dialog --enable-dawn-features=enable_renderdoc_process_injection
   ```
   Append the dev-server URL — e.g. `http://localhost:5189` for `12_ddgi` (its Vite
   `strictPort` is 5189).
3. Chrome starts as a blank window plus a small **"Google Chrome GPU"** dialog showing
   a **PID**. **Do not click OK yet.**
4. RenderDoc → `File > Inject into Process` → search that PID → double-click it.
5. Click **OK** on the GPU dialog. A RenderDoc overlay appears top-right → the hook is
   live.
6. Navigate to the WebGPU page. RenderDoc captures **every WebGPU frame** (no
   on-demand capture); close the tab to stop.

**Automate steps 1–6** with the repo's [`capture_webgpu.py`](../../../capture_webgpu.py)
script (launches Chrome with the full flag set, parses the GPU dialog PID, calls
RenderDoc's `InjectIntoProcess`, and collects the first `.rdc`).

## 1a. Get named shaders and objects (three.js labels)

The bare flag above gives anonymized dispatches — hard to tell the DDGI trace vs. blend
kernel apart. Add Dawn's label/symbol features so three.js `.setName()` calls and shader
names survive into RenderDoc:

```
--enable-dawn-features=enable_renderdoc_process_injection,use_user_defined_labels_in_backend,emit_hlsl_debug_symbols,disable_symbol_renaming
```

- `use_user_defined_labels_in_backend` exposes the buffer/texture names three.js sets
  (e.g. `ddgi_probeData`, `ddgi_rayDir`, the atlas textures in `12_ddgi`).
- `emit_hlsl_debug_symbols` + `disable_symbol_renaming` make shader source debuggable
  (**HLSL** after Dawn's WGSL→HLSL lowering — there is no WGSL in the capture).

For debug groups/markers (three's `renderer.compute()` labels), copy
`WinPixEventRuntime.dll` into the Chrome version dir (Microsoft PIX `.nupkg` → unzip →
`bin/x64`). **Re-copy after every Chrome update** — the updater wipes it.

## Caveats

- **F12 / PrtScrn will NOT capture WebGPU** — they grab the browser's own compositor,
  not the Dawn queue. Use the injection flow only.
- It captures **every frame**, not on-demand — capture lists get long fast.
- The D3D12 **thumbnail is not representative** of the final image (known quirk);
  trust the texture/step views, not the thumbnail.
- The **Chrome v144+** gate is the main friction today; keep a Canary install around
  specifically for GPU capture.
- Injection only works if the graphics API **hasn't initialized yet** — attach via the
  GPU startup dialog, not to an already-running tab.

## Sources

- [Profiling WebGPU with RenderDoc (toji.dev)](https://toji.dev/webgpu-profiling/renderdoc) — canonical Chrome D3D12 injection guide.
- [WebGPU + Chrome + RenderDoc notes (magcius gist)](https://gist.github.com/magcius/8b557684e986cf30d88e6caf79421cdc) — full flag set, WinPix debug-groups, ID3D12SharingContract caveat.
- [Dawn debugging.md — Capturing with RenderDoc in Chrome](https://dawn.googlesource.com/dawn/+/refs/heads/main/docs/dawn/debugging.md) — primary-source Dawn instructions.
