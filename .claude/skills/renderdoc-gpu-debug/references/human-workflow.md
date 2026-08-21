# Human 90% toolkit (agent version)

Canonical research: `renderdoc-human-experience.md` at the skill-repo root.

Do **not** start with a full draw dump, a full texture dump, or a shader `--trace`. Humans don't.

## Decision tree (first tool)

| User says | First action | Then |
|-----------|--------------|------|
| Mesh / geometry / missing faces / distorted | Mesh Viewer: **input vs VS output** (sample vertices + indices) | If input already bad → CPU/importer, stop. If output bad → VS constants / matrices |
| Wrong colour / tint / black / too bright | Texture Viewer inputs + **pick_pixel** | UVs/normals; PS constants; blend (`ColorWriteMask`, additive vs alpha) |
| Invisible / nothing on screen | Event Browser (right pass) + rasterizer/depth/blend/viewport | Pixel history at the expected pixel; empty draw; extra camera |
| This pixel / speck / hole | **Pixel history** (not a 15k-step trace) | Last *passing* fragment → debug that event |
| Shadows acne / missing / peter-pan | Shadow-pass RT + rasterizer depth bias | Light matrices in PS constants |
| Slow | Marker tree + GPU timings (filter editor UI first) | Nsight/PIX for hardware SOL — RenderDoc is not a profiler |
| What is this engine doing? | Markers + step + locked RT | Pipeline fill/cull/blend for the surprising pass |

## Token funnel that matches humans

```
1. Frame summary / passes          → pick a marker (Unity: Camera.Render)
2. Filtered draws                  → one suspect EID
3. pick_pixel OR mesh sample       → tiny JSON
4. pixel_history OR pipeline RS/DS/OM
5. shader constants / one texture export
6. --trace / full get_texture_data  only if still stuck
```

## Unity default filter

Always, unless the user asked about editor UI:

- `marker_filter = "Camera.Render"`
- exclude: `GUI.Repaint`, `UIR.DrawChain`, `GUITexture.Draw`, `UGUI.Rendering.RenderOverlays`, `PlayerEndOfFrame`, `EditorLoop`

rdc-cli: `rdc draws --pass "Camera.Render"`. MCP: `get_draw_calls(preset="unity_game_rendering")`.

## Capture hygiene before blaming the GPU

- Named resources + nested debug markers, or the Event Browser is a haystack.
- Shader debug info: D3D `/Zi` `/Od`; Vulkan `-gVS` or `-fspv-debug=vulkan-with-source`. Unity: `#pragma enable_d3d11_debug_symbols`.
- Shader debug is D3D11/D3D12/Vulkan only. Wave/quad intrinsics often disable or lie in pixel debug.
- Heisenbug: capture overlay changes timings and memory. If the bug vanishes, disable async compute / freeze the sim, don't assume the capture is innocent.
- Replay is not a perfect reproduction. Validation layers catch invalid API use; RenderDoc will not.

## Tool split

- **RenderDoc**: why this pixel/mesh/binding looks wrong.
- **Nsight** (desktop NVIDIA): GPU trace / SOL / SM stalls.
- **PIX**: D3D12/Xbox, shader PDB reload, some RT cases RD crashes on.

## rdc-cli mapping

| Human window | Command |
|--------------|---------|
| Event Browser | `rdc draws --pass …`, `rdc passes` |
| Texture Viewer + pick | `rdc rt EID -o png` then Read; `rdc pick-pixel X Y EID` |
| Pixel History | `rdc pixel X Y EID --json` |
| Pipeline RS/DS/OM | `rdc pipeline EID rs\|ds\|om --json` |
| Mesh in vs out | `rdc mesh EID --json` / `rdc debug vertex EID 0` |
| Overlays | `rdc rt EID --overlay wireframe\|depth\|…` |
