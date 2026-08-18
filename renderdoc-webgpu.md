# RenderDoc for the three.js games in this repo

> How to capture and inspect a frame from any VibeGames three.js project with
> [RenderDoc](https://renderdoc.org/). Written for the projects in this repo that
> use `WebGPURenderer` (e.g. `12_ddgi`) and `WebGLRenderer` (e.g. `11_blackhole`,
> `7_hotlineShanghai`).

## TL;DR

RenderDoc has **no native WebGPU support**. You capture WebGPU by hooking the
*browser's* underlying backend (D3D12 on Windows), not the Dawn queue directly.
For WebGL projects you capture directly. The whole WebGPU flow is a Chrome +
RenderDoc process-injection dance; WebGL is a simple "Launch Application".

| Backend | Projects | Method |
|---|---|---|
| WebGPU (D3D12) | `12_ddgi`, any `WebGPURenderer` work | Chrome v144+ D3D12 process injection (§1) |
| WebGL / WebGL2 | `11_blackhole`, `7_hotlineShanghai` | Direct capture (§3) |

---

## 1. WebGPU — capture via Chrome's D3D12 backend

**Requirements:** Windows only, **Chrome v144+** (at time of writing, only in
Canary), RenderDoc installed.

1. **Fully exit Chrome** (system-tray icon → "Exit"). Chrome keeps running in the
   background otherwise and won't attach.
2. **First launch only:** RenderDoc → `Tools > Settings` → check **"Enable
   Process Injection"** → restart RenderDoc.
3. **Open a command prompt (not PowerShell)** and launch Chrome:
   ```
   set RENDERDOC_HOOK_EGL=0 && "<Chrome Dir>\chrome.exe" --no-sandbox --disable-gpu-sandbox --disable-direct-composition --gpu-startup-dialog --enable-dawn-features=enable_renderdoc_process_injection
   ```
   Append your dev-server URL to auto-open — e.g.
   `http://localhost:5189` for `12_ddgi` (its Vite port is `strictPort: 5189`).
4. Chrome starts as a blank window plus a small **"Google Chrome GPU"** dialog
   showing a **PID**. **Do not click OK yet.**
5. RenderDoc → `File > Inject into Process` → search that PID → double-click it.
6. Click **OK** on the GPU dialog. A RenderDoc overlay appears top-right → the
   hook is live.
7. Navigate to the WebGPU page. RenderDoc captures **every WebGPU frame**
   (no on-demand capture); close the tab to stop.

### 1a. Get named shaders and objects (three.js labels)

The bare flags above give anonymized dispatches — hard to tell which is the DDGI
trace vs. blend kernel. Add Dawn's label/symbol features so three.js `.setName()`
calls and shader names survive into RenderDoc:

```
--enable-dawn-features=use_user_defined_labels_in_backend,emit_hlsl_debug_symbols,disable_symbol_renaming
```

- `use_user_defined_labels_in_backend` exposes the buffer/texture names three sets
  (e.g. `ddgi_probeData`, `ddgi_rayDir`, the atlas textures in `12_ddgi`).
- `emit_hlsl_debug_symbols` + `disable_symbol_renaming` make shader source
  debuggable (HLSL after Dawn's WGSL→HLSL lowering).

For debug groups/markers (three's `renderer.compute()` labels), copy
`WinPixEventRuntime.dll` into the Chrome version dir (Microsoft PIX `.nupkg` →
unzip → `bin/x64`). **Re-copy after every Chrome update** — the updater wipes it.

---

## 2. What to inspect for `12_ddgi` (concrete)

Each frame is roughly **3 compute dispatches → 1–2 draws**:
trace → blend (irradiance + distance) → border, then the lambert scene + atlas
overlay.

- **The two `StorageTexture` atlases** (irradiance `8×8`/probe, distance
  `18×18`/probe) — step to the blend pass and view them as textures to see the
  live probe field; confirm the 1-texel border ring vs. interior.
- **The `ddgi_rayData` buffer** — read it back in RenderDoc and confirm
  per-(probe·ray) hit distance / emissive matches `window.__ddgi.readProbeSummary()`.
- **Shader source** (with `emit_hlsl_debug_symbols`) — step through the Chebyshev
  reject branch in the M3 query to debug it without console logging.

---

## 3. WebGL projects — direct capture

RenderDoc captures WebGL/WebGL2 natively — no injection gymnastics. Launch the
browser under RenderDoc's `File > Launch Application`, or follow
[edw.is/renderdoc-webgl](https://edw.is/renderdoc-webgl/). Relevant for
`11_blackhole` (full-screen fragment ray-march) and `7_hotlineShanghai` (the
WebGL2 Radiance Cascades ping-pong pipeline).

---

## Caveats

- **F12 / PrtScrn will NOT capture WebGPU** — they grab the browser's own
  compositor, not the Dawn queue. Use the injection flow only.
- It captures **every frame**, not on-demand — capture lists get long fast.
- The D3D12 **thumbnail is not representative** of the final image (known quirk);
  trust the texture/step views, not the thumbnail.
- The **Chrome v144+** gate is the main friction today; keep a Canary install
  around specifically for GPU capture.
- Injection capture only works if the graphics API **hasn't initialized yet** —
  attach via the GPU startup dialog, not to an already-running tab.

## When to reach for RenderDoc vs. the in-repo hooks

RenderDoc is a **frame/render inspector**, not a profiler. For `12_ddgi`
specifically: use it when you need to *see exactly what a texture/buffer holds at
a given dispatch* (e.g. "is the atlas border correct?"). For *"is my GI correct /
why is it dim or slow"*, the in-repo debug hooks
(`window.__ddgi.readProbeSummary()` + probe gizmos) and the
`threejs-debug-profiler` skill iterate faster.

---

## Sources

- [Profiling WebGPU with RenderDoc (toji.dev)](https://toji.dev/webgpu-profiling/renderdoc) — canonical Chrome D3D12 injection guide.
- [WebGPU + Chrome + RenderDoc notes (magcius gist)](https://gist.github.com/magcius/8b557684e986cf30d88e6caf79421cdc) — full flag set, WinPix debug-groups, ID3D12SharingContract caveat.
- [RenderDoc WebGL capture (edw.is)](https://edw.is/renderdoc-webgl/) — direct path for WebGL projects.
- [Dawn debugging.md — Capturing with RenderDoc in Chrome](https://dawn.googlesource.com/dawn/+/refs/heads/main/docs/dawn/debugging.md) — primary-source Dawn instructions.
