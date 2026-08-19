# JOURNEY — RenderDoc Agent Skill & WebGPU/WebGL Capture

> Project history in two-column form. **Me** = the human driving; **You** = the agent partner.
> Started 2026-08-18. This repo (`renderdoc-skill/`) holds the Claude skill, the WebGPU/WebGL
> capture scripts, and the post-capture inspection guidance. The `rdc_harness` verification +
> orchestrator implementation lives in `RenderDocMCP/`.

## The spec documents (requirements)

- `renderdoc-agent-interview-guide.md` — token funnel, MCP-vs-Skill split, anomaly rules, SAM chunking, semi-transparent particles.
- `renderdoc-AI-Agent-token高效指南.md` — Layer1/Layer2/Resource summaries, auto-red-flags, early-stop.
- `renderdoc-perception-agent-design.md` — L1 deterministic + L2 behavioral verification, shader edit/replay loop, fix report.

## WebGPU capture support (2026-08-18)

> A first workstream: the skill (`renderdoc-skill`) covered post-capture *inspection*, but the
> **capture side** of `renderdoc-webgpu.md` (§1/§1a) and the `12_ddgi` recipe (§2) were missing
> from both repos, along with any WebGPU/Dawn knowledge.

| Me | You |
|---|---|
| Reviewed `renderdoc-webgpu.md` against both repos and ruled: both cover only the inspect half; Chrome D3D12 process-injection capture, Dawn flags, and the `12_ddgi` recipe are all missing. Handed over an update plan. | Executed: `renderdoc-skill` gained `references/webgpu-capture.md` (port of §1/§1a/caveats, Windows-only) and `capture_webgpu.py` (launch Chrome → parse GPU-dialog PID → `rd.InjectIntoProcess` → collect `.rdc`); `debugging-recipes.md` gained Recipe 7 (`12_ddgi`); `SKILL.md` gained WebGPU/WebGL/Chrome/Dawn/three.js trigger words + a §2 "WebGPU / WebGL (browser)" subsection. `RenderDocMCP` got a one-line WebGPU(D3D12) compatibility note — no capture added (the extension runs in RenderDoc's Python 3.6 sandbox and can't drive Chrome). |
| — | Verified: `capture_webgpu.py` `py_compile` clean; `RenderDocMCP` 63 tests green. Left `rdc doctor` + a real `capture_webgpu.py` run for a host with Chrome Canary v144+ + RenderDoc. |
| "git add files and push to remote" | Committed + pushed both repos (`renderdoc-skill` master `10c48db`; `RenderDocMCP` main `cd04063`). |
| "更新readme成中文，更新我们所做，更新journey然后push to remote" | Translated `renderdoc-skill/README.md` to Chinese (WebGPU section + 7 recipes + file structure); restored the Chinese WebGPU note in `RenderDocMCP/README.md`; appended this section to both JOURNEYs; pushed. |

### Deliverables

- `renderdoc-skill`: `references/webgpu-capture.md` (new), `capture_webgpu.py` (new), `debugging-recipes.md` Recipe 7, `SKILL.md` trigger/scope/§2 updates.
- `RenderDocMCP`: README WebGPU(D3D12) compatibility note (docs only).

## WebGL capture breakthrough + ReSTIR GI debug, VibeGames/6_patapon3D (2026-08-19)

> Second workstream: use the skill end-to-end against a real WebGL2 game (ReSTIR GI in
> `VibeGames/6_patapon3D`, AMD RX 9070 XT, Playwright Chromium-1234). The WebGPU capture
> recipe's `--gpu-startup-dialog` flow turned out to be broken on Chrome ≥ ~120; a new
> WebGL recipe was discovered, and the debug itself reached a closed-loop root cause at
> DXBC instruction level.

| Me | You |
|---|---|
| "use renderdoc-gpu-debug skill to debug restir algo under 9_3dplatform, dump result to report doc" — corrected the path myself: `9_3dplatform` is design-docs only; the monorepo's only ReSTIR implementation is `6_patapon3D` (`src/core/reservoir.ts` + `src/engine/raytrace/VoxelRaycaster.ts`) | Static review first → hypotheses H1 (GI candidate targetPdf missing cosθ → unbounded lum·π/cosθ), H2 (shadow disk & GI direction share one hash seed), H3/H5/H6/H7 (history never cleared / hash precision / prevUv clamp / shadow EMA no surface check). Then capture: **9 failed paths, 1 success**. Broken: Chrome module allow-listing kills root-process injection; `--gpu-startup-dialog` no longer pauses Chrome 151/Chromium-1234; suspended-entry injection dies in chrome_elf boot; `--disable-direct-composition` removes present-frame boundaries. **Working recipe**: direct Chrome target + `--in-process-gpu` + direct composition + `rdc capture --keep-alive`, with ALL paths forward-slashed (rdc-cli `capture.py:137` POSIX `shlex.join` corrupts `\` args). |
| — | Captured `restir_frame600.rdc` (D3D11, 198 events; game MRT pass = EID 33, targets 0=color/1=GI reservoir/2=surface, 1061×811). `scan_inf.py`: reservoir history has **279 non-finite texels** — 152 px weight=+Inf (fence top edge + boss drum band), 127 px rgb=(−Inf³)+a=NaN (left Patapon region). Bisect captures (frame 5/30/60): poison seeded within the first ~30 frames, stable ~280-295 texels through frame 600 — self-sustaining, never heals, never spreads. |
| — | Full pixel-debug trace (`dbg_25_326_f30.json`, 15104 steps) + DXBC disasm (`ps33.dxbc`, 3794 lines; trace instr N ↔ listing N−1) traced the **−Inf seed to its birth instruction**: bounce ray direction with an exactly-0 component (axis-aligned voxel normals + rng.x≈0 ⇒ dg≈n — systematic, not rare) → `invOrZero`=1e30 guard → `sign(rd)=0`→step=0 → that axis's tMax=(boundary−ro)·(−1e30)=**−3.1e29** wins the DDA min-select → real voxel hit with garbage t → caller's one-sided `gh.t < 40.0` accepts it → bounceShade fog `1−exp(−t·0.012)` = `1−exp(+5.45e27)` = **−Inf radiance** → candTargetPdf=−Inf → weightSum=NaN → (−Inf³, NaN) written to half-float history → permanent self-lock. Same fog-overflow pattern at 4 shader sites, primary ray path included. |
| — | H1 confirmed against NVIDIA RTXDI official source (GitHub): `RAB_LightSampling.hlsli:88` target PDF = `luminance(Li·BRDFtimesNoL)` with Lambert = cosθ/π → candidate weight = luminance(Li), cosθ cancels, bounded; plus `c_MaxIndirectRadiance = 10` clamp before MakeGIReservoir. Ours omits both ⇒ up to 31416× weight at the cosθ clamp floor. |
| "update skill repo with your success learnings, git add + push; continue debug restir" | New `references/webgl-chrome-capture.md` (verified recipe + all 9 failed paths + shlex gotcha); SKILL.md §2 WebGL bullet rewritten; `webgpu-capture.md` regression note. Committed `5d54186`, pushed origin master. Continued debug to closure; wrote the full evidence chain + F0–F6 fix table into `6_patapon3D/docs/renderdoc-restir-debug-report.md`. No game source modified — fixes queued for confirmation. |
| "update JOURNEY.md" | Appended this section. |

### Deliverables

- `renderdoc-skill` (pushed `5d54186`): `references/webgl-chrome-capture.md` (new), `SKILL.md` §2, `references/webgpu-capture.md` regression note.
- `6_patapon3D` (uncommitted, docs only): `docs/renderdoc-restir-debug-report.md` — capture recipe, EID 33 layout, poison quantification, bisect, DXBC-level root-cause chain, RTXDI comparison, F0–F6 prioritized fixes, verification loop.
- Analysis infra in `%TEMP%\kilo\renderdoc\`: `scan_inf.py` / `scan_rs.py` / `scan_all_last.py` / `find_neginf.py` non-finite texel scanners, `dbg_25_326_f30.json` trace, `ps33.dxbc` disasm, bisect captures `restir_frame{5,30b,60}_frame*.rdc`.

### Operational lessons (now in skill docs + report §6)

- Always use a **named session** (`rdc --session <name> open ...`) — parallel agent sessions hijack the default one; daemon corrupts after script errors (GetTextureData returns 0 bytes for everything) → `close` + reopen fixes it.
- renderdoc.pyd **1.41 API gaps**: no `GetDrawcalls`/`GetActions` (use `GetRootActions()`), no `ActionDescription.name`, usage enum is `rd.ResourceUsage` (prints as int), output descriptor attr is `.resource`, D3D11 `GetPipelineState().GetOutputTargets()` returns empty — use `GetUsage(rid)` to find write events.
- Texture **initial contents read back as 0 bytes** — must `SetFrameEvent(<write event>, False)` before `GetTextureData`.
- Trace JSON needs `encoding='utf-8-sig'` (BOM); rdc-cli `capture` auto-names `<name>_frame<N>.rdc`; `--frame N` counts Chrome process presents (game canvas appears ~frame 10-25).

## Final state

- **Deliverable**: `renderdoc-skill/` — a Claude skill for RenderDoc debugging (trigger-word routing, 7 recipes, references), plus WebGPU + WebGL capture scripts (`capture_webgpu.py` + `references/webgl-chrome-capture.md`).
- **Coverage**: Doc1 (token funnel + MCP/Skill split + anomaly rules), Doc2 (Layer1/Layer2 summaries + `auto_red_flags`), Doc3 (L1 deterministic + L2 behavioral + shader edit/replay loop + fix report). The implementation side lives in `RenderDocMCP/rdc_harness/`.
- **Tests**: `capture_webgpu.py` `py_compile` clean; `RenderDocMCP` 63 tests green at the time of the WebGPU push; the WebGL push was validated against a real capture (`restir_frame600.rdc`).
- **Pending manual verification**: `rdc doctor` + `capture_webgpu.py` against `12_ddgi`, confirming the `.rdc` exposes named `ddgi_probeData` / `ddgi_rayDir` resources (needs Chrome Canary v144+ + RenderDoc).
- **Status from ReSTIR workstream**: Skill pushed; game source untouched. **Next**: apply fixes F0a/F0b/F0d + F1 (seed + propagation lock) in one commit, F2 (cosθ target PDF) separately for A/B, then re-capture → `scan_inf.py` must read 0 non-finite texels.

---

## Cross-repo workstreams

The **`rdc_harness` verification + orchestrator core** (the gap closure for Doc3 §5, the critic rounds, the RenderDoc-side I/O wiring) landed in `RenderDocMCP/`. This repo's role is the Claude skill (`SKILL.md` + `references/`) and the WebGPU/WebGL capture scripts — the inspection guidance, not the verification engine. See `RenderDocMCP/JOURNEY.md` for the workstream.
