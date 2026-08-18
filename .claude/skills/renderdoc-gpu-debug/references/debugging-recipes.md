# GPU Debugging Recipes

Extended debugging workflows with expected output shapes and lux-playground-specific examples.

## Recipe 1: Object is Invisible

**Symptoms**: An object that should be visible in the scene is not rendered.

### Step-by-step

```bash
# 1. Open the capture
rdc open D:/renderdoc/captures/frame.rdc

# 2. List all draws to find the expected object
rdc draws --json --limit 50

# 3. If you know the pass name, filter:
rdc draws --pass "Raster Pass" --json

# 4. Check rasterizer state — is back-face culling removing it?
rdc pipeline EID rs --json
```

**Expected rasterizer output shape:**
```json
{
  "CullMode": "Back",
  "FrontFace": "CounterClockwise",
  "DepthClipEnable": true,
  "FillMode": "Solid",
  "Viewports": [{"X": 0, "Y": 0, "Width": 512, "Height": 512, "MinDepth": 0.0, "MaxDepth": 1.0}]
}
```

**Common causes:**
- `CullMode` is `Front` when geometry has clockwise winding (or vice versa)
- Viewport is zero-sized or doesn't cover the object
- Scissor rect clips the object
- Depth test rejects it (object behind another or outside depth range)
- Blend state has `ColorWriteMask: 0` (writing disabled)
- Vertex transform puts vertices off-screen

```bash
# 5. Check depth state
rdc pipeline EID ds --json

# 6. Verify the draw is issuing geometry
rdc draw EID --json
# Expected: VertexCount > 0, InstanceCount > 0

# 7. Debug a vertex to check transform
rdc debug vertex EID 0 --json
# Look at gl_Position / SV_Position — is it in NDC [-1,1]?

# 8. Close
rdc close
```

---

## Recipe 2: Colors Are Wrong

**Symptoms**: Object renders but with incorrect colors, wrong texture, or unexpected tint.

### Step-by-step

```bash
rdc open D:/renderdoc/captures/frame.rdc

# 1. Export what we see
rdc rt EID -o D:/renderdoc/captures/analysis/wrong_color.png
# Then use Read tool to view it

# 2. Pick the pixel
rdc pick-pixel 256 256 EID --json
```

**Expected pick-pixel output:**
```json
{
  "x": 256, "y": 256,
  "r": 0.502, "g": 0.0, "b": 0.0, "a": 1.0
}
```

```bash
# 3. Check bound textures
rdc bindings EID --json
# Look for sampler2D / texture2D bindings, verify resource IDs

# 4. Export the bound texture to verify it's correct
rdc texture RESID -o D:/renderdoc/captures/analysis/bound_tex.png

# 5. Check constant buffers for material colors
rdc shader EID ps --constants --json

# 6. Check blend state
rdc pipeline EID om --json
```

**Expected output merger shape:**
```json
{
  "BlendState": {
    "Blends": [{
      "Enabled": false,
      "Source": "One",
      "Destination": "Zero",
      "Operation": "Add",
      "ColorWriteMask": 15
    }]
  },
  "DepthStencilState": {
    "DepthEnable": true,
    "DepthFunc": "LessEqual"
  }
}
```

```bash
# 7. If still unclear, trace the pixel shader
rdc debug pixel EID 256 256 --trace

rdc close
```

**Common causes:**
- Wrong texture bound (check resource ID matches expected)
- Constant buffer has wrong material color
- Blend state is additive (`Source: One, Dest: One`) when it should be alpha
- Texture format mismatch (sRGB vs linear, swizzled channels)
- Fragment shader has hardcoded color or wrong calculation

---

## Recipe 3: Shadows Are Broken

**Symptoms**: Shadows are too dark, too blocky, have peter-panning, acne, or are missing entirely.

### lux-playground specifics

lux-playground uses debug markers "Shadow Pass" and "Raster Pass". The shadow map is rendered first, then sampled during the raster pass.

### Step-by-step

```bash
rdc open D:/renderdoc/captures/frame.rdc

# 1. List passes to confirm structure
rdc passes --json

# 2. Find shadow pass draws
rdc draws --pass "Shadow Pass" --json

# 3. Get the last shadow pass draw (final shadow map state)
SHADOW_EID=$(rdc draws --pass "Shadow Pass" -q | tail -1)

# 4. Export the shadow map
rdc rt $SHADOW_EID -o D:/renderdoc/captures/analysis/shadow_map.png
# View with Read tool — look for:
#   - Resolution (too small = blocky shadows)
#   - Coverage (objects should appear as silhouettes)
#   - Depth range (should use full [0,1] range)

# 5. Check shadow map resolution via bindings
rdc bindings $SHADOW_EID --json
# Look at render target dimensions (e.g., 1024x1024 or 2048x2048)

# 6. Check depth bias (prevents shadow acne)
rdc pipeline $SHADOW_EID rs --json
```

**Expected rasterizer with depth bias:**
```json
{
  "DepthBias": 1.25,
  "SlopeScaledDepthBias": 1.75,
  "DepthBiasClamp": 0.0
}
```

**Shadow acne**: DepthBias too low (or zero). Increase bias.
**Peter-panning**: DepthBias too high. Reduce bias.
**No shadows**: Bias doesn't help — check light matrices in constants.

```bash
# 7. Find the raster pass that samples the shadow
LIGHT_EID=$(rdc draws --pass "Raster Pass" -q | head -1)

# 8. Check the fragment shader for shadow sampling
rdc shader $LIGHT_EID ps --source
# Look for: shadow map sampling, bias, PCF kernel, comparison sampler

# 9. Check light matrices and shadow parameters in constants
rdc shader $LIGHT_EID ps --constants --json
# Look for: lightViewProj, shadowBias, shadowMapSize, pcfRadius

# 10. Debug a pixel that should be in shadow
rdc debug pixel $LIGHT_EID 300 400 --trace
# Follow the shadow calculation through the trace

# 11. Export the final frame for comparison
rdc rt $LIGHT_EID -o D:/renderdoc/captures/analysis/final_frame.png

rdc close
```

**Common shadow issues:**
| Symptom | Likely Cause | Check |
|---------|-------------|-------|
| Blocky/pixelated | Low shadow map resolution | bindings → RT dimensions |
| Acne (self-shadowing) | Depth bias too low | pipeline rs → DepthBias |
| Peter-panning (detached) | Depth bias too high | pipeline rs → DepthBias |
| Missing entirely | Shadow map not sampled | bindings on raster pass |
| Wrong direction | Light matrix incorrect | shader constants → lightViewProj |
| Hard edges | No PCF / PCF radius = 0 | shader source → PCF code |

---

## Recipe 4: Performance Is Bad

**Symptoms**: Low FPS, high GPU utilization, large frame time.

### Step-by-step

```bash
rdc open D:/renderdoc/captures/frame.rdc

# 1. Get frame statistics
rdc stats --json
# Look for: total draws, total events, per-pass breakdown

# 2. Check draw count per pass
rdc passes --json
# Excessive draws in any single pass?

# 3. Look for the most expensive draws (if counters available)
rdc counters --list
# If GPU counters are available:
rdc counters --name "duration" --json

# 4. Check for massive textures
rdc resources --type Texture --sort size --json
# Textures > 16MB at high resolution may indicate uncompressed or oversized

# 5. Look for redundant state changes
rdc events --limit 200 --json

# 6. Check for overdraw with wireframe overlay
rdc rt EID --overlay wireframe -o D:/renderdoc/captures/analysis/wireframe.png
# Dense wireframe = high geometric complexity

rdc close
```

**Performance red flags:**
- Draw count > 1000 per pass (consider batching/instancing)
- Textures > 32MB (consider compression, mipmaps)
- Multiple passes with identical draw calls (redundant work)
- Many small draws with state changes between each

---

## Recipe 5: What Changed Between Two Frames

**Symptoms**: Regression between two builds, or before/after a code change.

### Step-by-step

```bash
# Capture both frames
cd D:/shaderlang && rdc capture -o D:/renderdoc/captures/before.rdc -- ./app --args
# ... make code change ...
cd D:/shaderlang && rdc capture -o D:/renderdoc/captures/after.rdc -- ./app --args

# Quick summary
rdc diff D:/renderdoc/captures/before.rdc D:/renderdoc/captures/after.rdc --shortstat

# Detailed comparisons
rdc diff D:/renderdoc/captures/before.rdc D:/renderdoc/captures/after.rdc --draws --json
rdc diff D:/renderdoc/captures/before.rdc D:/renderdoc/captures/after.rdc --passes --json
rdc diff D:/renderdoc/captures/before.rdc D:/renderdoc/captures/after.rdc --resources --json

# Visual diff of final framebuffer
rdc diff D:/renderdoc/captures/before.rdc D:/renderdoc/captures/after.rdc \
    --framebuffer \
    --diff-output D:/renderdoc/captures/analysis/diff.png
# View diff.png with Read tool — changed pixels will be highlighted

# Compare pipeline state at a specific draw
rdc diff D:/renderdoc/captures/before.rdc D:/renderdoc/captures/after.rdc --pipeline EID --json
```

---

## Recipe 6: Debug This Pixel

**Symptoms**: User points at a specific pixel and asks "why does this look like that?"

### Step-by-step

```bash
rdc open D:/renderdoc/captures/frame.rdc

# 1. Get pixel history — who wrote to this pixel?
rdc pixel X Y --json
```

**Expected pixel history output shape:**
```json
[
  {
    "eid": 42,
    "name": "DrawIndexed(360)",
    "passed": true,
    "pre": {"r": 0.0, "g": 0.0, "b": 0.0, "a": 0.0},
    "post": {"r": 0.8, "g": 0.2, "b": 0.1, "a": 1.0},
    "depth_passed": true,
    "stencil_passed": true
  },
  {
    "eid": 67,
    "name": "DrawIndexed(180)",
    "passed": false,
    "failure_reason": "depth_test"
  }
]
```

```bash
# 2. Go to the draw that produced the final color (last passing entry)
# 3. Read the final pixel value
rdc pick-pixel X Y 42 --json

# 4. Get the shader execution summary
rdc debug pixel 42 X Y --json

# 5. If you need the full trace
rdc debug pixel 42 X Y --trace

# 6. Check specific variable values at a line
rdc debug pixel 42 X Y --dump-at 15
# Shows all variable values when execution reaches line 15

# 7. Cross-reference with pipeline state
rdc pipeline 42 --json

# 8. Export the render target at that draw to see context
rdc rt 42 -o D:/renderdoc/captures/analysis/at_draw_42.png

rdc close
```

**Interpretation guide:**
- If pixel history shows `passed: false` with `failure_reason: depth_test` — the object is behind something
- If pixel history shows `passed: false` with `failure_reason: stencil_test` — stencil mask is blocking
- If the final color differs from shader output — blend state is modifying it
- If no entries in pixel history — nothing is drawing to that pixel (check viewport/scissor)

---

## Recipe 7: 12_ddgi — WebGPU DDGI probes (Chrome D3D12 capture)

**Symptoms**: The DDGI probe atlas or indirect lighting looks wrong — the 1-texel
border ring is off, the GI is dim, or the per-probe hit data doesn't match
`window.__ddgi.readProbeSummary()`.

**Prerequisite**: capture via `capture_webgpu.py` (Chrome D3D12 process injection — see
`references/webgpu-capture.md`). A WebGPU-on-D3D12 capture is a **plain D3D12 capture**,
so the standard `rdc` inspect commands apply. Each frame is roughly **3 compute
dispatches** (trace → blend → border) then **1–2 draws** (lambert scene + atlas overlay).
With Dawn labels enabled, dispatches are named by three.js `.setName()` calls.

### Step-by-step

```bash
rdc open D:/renderdoc/captures/ddgi.rdc

# 1. Find the blend dispatch (with labels enabled, it's named, not anonymous)
rdc draws --limit 50
rdc events --filter "*blend*" --json
BLEND_EID=...   # the blend compute dispatch EID
```

**Expected shape**: the blend dispatch is a `Dispatch` event (not `DrawIndexed`) writing
into two `StorageTexture` atlases.

```bash
# 2. View the two StorageTexture atlases (irradiance 8×8/probe, distance 18×18/probe)
rdc bindings $BLEND_EID --json
# Find the atlas resource IDs, then export them:
rdc texture IRRADIANCE_RESID -o D:/renderdoc/captures/analysis/ddgi_irradiance.png
rdc texture DISTANCE_RESID   -o D:/renderdoc/captures/analysis/ddgi_distance.png
# View both with the Read tool.
```

**Confirm** the 1-texel border ring vs. interior on the irradiance atlas — the border
ring should read clearly separated from the interior probe values.

```bash
# 3. Read the ddgi_rayData buffer (per-(probe·ray) hit distance / emissive)
rdc resources --name ddgi_rayData --json
rdc buffer RAYDATA_RESID -o D:/renderdoc/captures/analysis/ddgi_rayData.bin
```

**Cross-check** the read-back values against `window.__ddgi.readProbeSummary()` in the
browser console. Mismatch = the capture is showing a stale or wrong dispatch.

```bash
# 4. Step through the Chebyshev reject branch in the trace/blend shader source
rdc shader $BLEND_EID cs --source        # HLSL (Dawn lowered WGSL→HLSL)
rdc shader $BLEND_EID cs --constants --json
# Debug a specific compute thread if needed:
rdc debug thread $BLEND_EID GX GY GZ TX TY TZ --json

rdc close
```

### Interpretation

| Symptom | Likely cause | Check |
|---------|-------------|-------|
| Border ring wrong | 1-texel border not written/read as expected | atlas PNGs |
| Dim / no GI | ray data zeroed or trace dispatch not run first | `ddgi_rayData` buffer |
| Atlas values stale | looking at trace, not blend, dispatch | `rdc events --filter "*blend*"` |
| Can't find named resources | Dawn label feature off | re-capture with full `DAWN_FEATURES` |

**When to use RenderDoc vs. in-repo hooks**: use RenderDoc to *see exactly what a
texture/buffer holds at a given dispatch* (e.g. "is the atlas border correct?"). For
"is my GI correct / why dim or slow", the in-repo `window.__ddgi.readProbeSummary()` +
probe gizmos and the `threejs-debug-profiler` skill iterate faster.
