# RenderDoc for AI Agent：Token 高效指南

> 人类靠视觉并行扫描，AI 靠序列化阅读 + 上下文窗口。**漏斗化、模板化、可停止** 是三个核心。

---

## 0. 为什么人类方法对 AI 不管用

| 维度 | 人类 | AI Agent |
|------|------|---------|
| 信息获取 | 视觉并行 + 颜色高亮 | 序列化 token 读入 |
| 上下文 | 经验直觉 | 上下文窗口（几万到几十万 token） |
| 一次 capture | 几 GB 数据，几万个 Draw | 全部塞进去直接 OOM |
| 速度 | 看图秒判 | 读完才知道 |
| 经验先验 | 脑子里有 | 必须在 prompt 里显式给 |

**结论**：AI 看 RenderDoc 必须是 **"漏斗 + 摘要"**，不能像人一样从顶到底全扫。

---

## 1. 三个核心原则

### 原则 1：摘要优先（Summary First）
- **永远先拿总览**，再决定要不要下钻。
- 拿一个 500-token 的 frame summary，比把 5 万个 Draw Call 一次性读进来有用 100 倍。

### 原则 2：按需下钻（Drill-Down on Demand）
- 每一层摘要都要回答"要不要继续？"
- 不要"无差别深挖"——找到瓶颈或确认无问题，就停。

### 原则 3：早停（Stop When Sufficient）
- 任何指标出现"明显异常" → 直接锁死它，开始第二层。
- 不要为了"完整性"读完全部数据。

---

## 2. 三层漏斗（核心架构）

```
┌──────────────────────────────────────────┐
│ Layer 1: Frame-Level Summary    ~500 tok │  ← 一开始就拿这个
│   "这帧整体有没有问题？瓶颈大概在哪？"   │
└──────────────┬───────────────────────────┘
               │ 定位到可疑 Pass（1-3 个）
               ▼
┌──────────────────────────────────────────┐
│ Layer 2: Pass-Level Summary    ~300 tok/P│  ← 只读可疑的
│   "这个 Pass 慢在哪个阶段？哪些 Draw 异 │     常？"
└──────────────┬───────────────────────────┘
               │ 定位到可疑 Draw / Resource（1-5 个）
               ▼
┌──────────────────────────────────────────┐
│ Layer 3: Resource Deep-Dive    按需加载   │  ← 精准下钻
│   "Shader/纹理/网格到底有什么问题？"    │
└──────────────────────────────────────────┘
```

**总 token 预算**：一次完整分析 ≈ **2,000-5,000 tokens**，不是 50,000+。

---

## 3. Layer 1：Frame-Level Summary 模板

让 RenderDoc（或自己写的脚本）输出**这样一个结构化摘要**：

```json
{
  "frame": {
    "total_gpu_ms": 18.4,
    "total_cpu_ms": 14.2,
    "fps_target_ms": 16.67,
    "fps_budget_status": "OVER",            // 是否超预算
    "bottleneck": "GPU-bound"               // CPU-bound / GPU-bound / Balanced
  },
  "api_stats": {
    "draw_calls": 1847,
    "dispatches": 64,
    "psos_used": 312,
    "state_changes": 892,
    "rt_switches": 23
  },
  "gpu_stage_breakdown_ms": {
    "input_assembler": 0.3,
    "vertex_shader": 1.8,
    "hull_domain": 0.0,
    "geometry_shader": 0.0,
    "rasterizer": 2.1,
    "pixel_shader": 9.7,      ← 占比 53%，重点关注
    "output_merger": 1.2,
    "compute_shader": 0.4
  },
  "memory_bandwidth": {
    "dram_read_gb_s": 145.2,
    "l2_throughput_pct": 62.3,
    "bandwidth_status": "healthy"            // healthy / approaching / saturated
  },
  "top_passes_by_ms": [
    {"name": "Lighting", "ms": 6.2, "draws": 412, "verdict": "suspect"},
    {"name": "PostProcess", "ms": 3.8, "draws": 28, "verdict": "normal"},
    {"name": "GBuffer", "ms": 2.4, "draws": 847, "verdict": "normal"},
    {"name": "UI", "ms": 1.9, "draws": 156, "verdict": "suspect"}
  ],
  "top_resources": {
    "largest_textures": ["T_skin_LOD0_4096", "T_envmap_2k_cube"],
    "uncompressed_textures": ["T_hero_normal_2k", "T_hero_orm_2k"],
    "unmipped_textures": ["T_ui_atlas_2k"]
  },
  "auto_red_flags": [                         // 脚本预判的明显问题
    "PS 占比 > 50% → Pixel-bound",
    "UI Pass 占比 > 10% → 检查 UI 重建",
    "L2 throughput 接近 100% → 带宽风险"
  ]
}
```

**AI 拿到这串 JSON 后，prompt 该怎么写**：
> "请基于以上 frame summary，定位最可能的瓶颈 Pass，给出下一步下钻建议（要分析哪些 Pass、哪些资源）"

**AI 的输出**：一个 200-500 token 的"诊断 + 下钻计划"。

---

## 4. Layer 2：Pass-Level Summary 模板

只对 Layer 1 标记为 `suspect` 的 Pass 加载。模板：

```json
{
  "pass": "Lighting",
  "total_ms": 6.2,
  "draw_count": 412,
  "stage_breakdown_ms": {
    "vs": 0.8,
    "ps": 4.9,         ← 重点
    "raster": 0.2,
    "om": 0.3
  },
  "top_draws_by_ms": [
    {"idx": 1547, "name": "Draw_Char_Main", "ms": 1.2, "triangles": 184K,
     "pixels_shaded": 12.3M, "vs_complexity": "high", "ps_complexity": "very_high",
     "rt": "HDRScene", "blend": "off", "depth_test": "less_eq"},
    {"idx": 1589, "name": "Draw_Hair_Shell", "ms": 0.6, "triangles": 89K,
     "pixels_shaded": 8.1M, "vs_complexity": "medium", "ps_complexity": "high",
     "rt": "HDRScene", "blend": "src_alpha,inv_src_alpha", "depth_test": "less_equal"}
  ],
  "psos_in_pass": {
    "unique_count": 38,
    "top_psos": ["PSO_Char_Skin", "PSO_Hair", "PSO_Eye"]
  },
  "batching_issues": {                       // 关键！
    "same_mesh_different_pso": 47,           // 47 个 Draw 是同 Mesh 不同 PSO
    "same_pso_different_mesh": 12,
    "potential_instancing_candidates": 89    // 89 个可合批的 Draw
  },
  "overdraw_estimate": 4.2,                  // 估算的 Overdraw 倍数
  "render_targets": ["HDRScene_2K", "GBufferA"],
  "verdict": "Pixel-bound + 合批机会大"
}
```

**AI 拿到后**：聚焦看 `top_draws_by_ms` 和 `batching_issues`，决定要不要下钻。

---

## 5. Layer 3：Resource Deep-Dive（精准下钻）

只下钻 Layer 2 标记的 1-5 个可疑资源。每个资源**只拿必要的部分**。

### 5.1 下钻 Shader
**不要**把整份 HLSL 源码塞进去。只输出：

```
PSO: PSO_Char_Skin
- 指令数: 487（高）
- 寄存器数: 64（高，限制 occupancy）
- 纹理采样: 8 次（超标）
- 算术 heavy 函数: 3x pow(), 2x exp()
- 分支: 12 个 if 嵌套
- texture fetch 字节数: 估计 256 KB/帧 (基于像素数 × 8 采样 × 4通道 × 2字节)
- 建议: 合并 4x BRDF lookup 为 LUT，precompute 菲涅尔
```

### 5.2 下钻纹理
**不要**塞 base64 图像。只输出：

```
Texture: T_hero_normal_2k
- 尺寸: 2048×2048
- 格式: RGBA8 (未压缩 → 应改为 BC5)
- 实际使用 mip: 0 (从未用过 mip → 缩到 1024 也无视觉损失)
- 内存: 16 MB (压缩后 4 MB)
- 每帧采样字节数: ~30 MB
- 建议: BC5 压缩 + 生成完整 mip
```

### 5.3 下钻 Mesh
**不要**塞顶点数据。只输出：

```
Mesh: Hero_Head
- 三角形: 184K
- 屏幕平均尺寸: 256x320 px
- LOD 0/1/2 切换距离: 8m / 20m / 50m
- 屏幕占比 < 0.5% 时仍在 LOD 0
- 建议: 增加 LOD 3 (>30m)，或屏幕占比 < 10% 时强制降级
```

---

## 6. 早停原则（Stop When Sufficient）

### 6.1 必须停的信号
- 找到了**单一明确瓶颈**（如 PS 占比 > 70% + PSO_Char_Skin 单一占比 > 50%）→ **直接给修复建议**
- 已经在推荐优化 → 不要再去查"还可能有什么"

### 6.2 不要做的事
- ❌ 把所有 Draw Call 列给 AI（10K+ token 浪费）
- ❌ 让 AI 自己枚举"可能的问题"（会发散，token 爆炸）
- ❌ 给 AI 看完整 shader 源码
- ❌ 一次分析 10 个 Pass（最多 3 个 suspect）

---

## 7. 工具链：让 RenderDoc 输出 token 友好的格式

### 7.1 用 `renderdoccmd` 导出

```bash
# 导出 capture summary
renderdoccmd replay /path/to/capture.rdc --replay-options="--summary"

# 导出 Performance Counter 数据为 CSV
renderocmd replay capture.rdc --counter "GPU Duration,VS Duration,PS Duration" --csv
```

### 7.2 用 Python 脚本预处理

推荐用 [renderdoc-python](https://github.com/evilangel-ru/renderdoc-python) 解析 .rdc：

```python
# 伪代码：生成 Layer 1 summary
def generate_frame_summary(rdc):
    return {
        "frame": extract_timing(rdc),
        "api_stats": count_api_calls(rdc),
        "stage_breakdown": get_counter_breakdown(rdc),
        "top_passes": aggregate_by_pass(rdc, top_n=10),
        "auto_red_flags": detect_anomalies(rdc)
    }
```

**关键点**：把"AI 推理"前置到"脚本统计"，AI 拿到的就是已经结构化、已统计、已排序的数据。

### 7.3 直接读 SQLite

`.rdc` 内部是 SQLite。可以用 `sqlite3 capture.rdc` 直接查：
- `Event` 表：所有 GPU 事件
- `Drawcall` 表：所有 Draw Call
- `Counter` 表：所有 Performance Counter
- `Texture` 表：所有纹理元数据

```sql
-- Top 10 最慢的 Pass
SELECT name, SUM(duration) as total_ms
FROM Drawcall
WHERE marker != ''
GROUP BY name
ORDER BY total_ms DESC
LIMIT 10;
```

---

## 8. 完整 Prompt 模板（给 AI Agent 用）

```markdown
你是一个渲染性能分析专家。系统会提供以下三段 JSON 数据（按漏斗顺序）：

## Layer 1: Frame Summary
<贴入 Layer 1 JSON>

## Layer 2: Pass Summary（仅可疑 Pass）
<贴入 1-3 个 Pass 的 Layer 2 JSON>

## 你的任务
1. 基于 Layer 1，**判断瓶颈类型**（CPU/VS/PS/Bandwidth/ROP），并给出依据（1-2 句话）
2. 基于 Layer 2 的可疑 Pass，**列出 3 个最可能的问题点**，每个用 1 句话说明
3. **决定是否需要 Layer 3 下钻**——如果需要，明确说"下钻 X 资源的 Y 方面"
4. **给出 3 条可执行的优化建议**，按 ROI 排序

## 约束
- 总输出 < 600 tokens
- 不要泛泛而谈，要引用具体数字
- 如果 Layer 1+2 已经足够判断，**直接给结论，不要再说"需要更多信息"**
- 不要列出所有 Pass，只说可疑的
```

---

## 9. Token 预算对照表

| 做法 | Token 量 | 价值 |
|------|---------|------|
| ❌ 读所有 5000 个 Draw Call | ~50,000 | 低（人看不完） |
| ❌ 贴完整 shader 源码 × 5 | ~20,000 | 低（绝大多数无关） |
| ❌ 贴纹理 base64 | ~100,000+ | 极低 |
| ✅ Layer 1 summary | ~500 | 极高 |
| ✅ Layer 2 summary × 3 Pass | ~900 | 高 |
| ✅ Layer 3 下钻 × 2 资源 | ~800 | 中（按需） |
| **合计：典型一次分析** | **~2,500** | 高 |

**优化后效率提升：~20x**。

---

## 10. 高级技巧

### 10.1 增量分析（Incremental）
- **不要一次分析一帧**，分析 3 帧（流畅帧、卡顿帧、典型帧）做 diff
- 同样 Layer 1 模板，但加 `delta_vs_baseline` 字段
- 关键问题往往在 delta 里，不在绝对值里

### 10.2 异常检测自动化
脚本侧预先跑这些规则，避免让 AI 自己判断：

| 规则 | 阈值 | 严重度 |
|------|-----|-------|
| PS 耗时占比 > 50% | 100% | 高 |
| 单 Pass > 5ms | 8ms | 高 |
| Draw Call > 3000 | 5000 | 中 |
| Overdraw > 8x | 10x | 中 |
| 纹理未压缩 | 任意 | 低 |
| 同一 Mesh 多次 Draw 且未合批 | 10+ | 中 |
| UI Pass > 2ms | 5ms | 中 |
| L2/DRAM 带宽 > 80% | 95% | 高 |

把"判断"留给 AI，把"统计"留给脚本。

### 10.3 让 AI 给出"测试假设"而不是"分析"
不要让 AI 漫游分析。**让 AI 提假设**：
> "我假设是 PSO_Char_Skin 的 PS 指令过多导致 Pixel-bound。请帮我下钻 PSO_Char_Skin 的 shader 统计 + 它的 47 次 Draw 是否能用合批消除。"

这样一次只验证 1-2 个假设，token 极省。

---

## 11. 关键反例（不要这样做）

| 反例 | 为什么坏 | 应该怎样 |
|------|---------|---------|
| 问 AI "分析这个 capture" | 太开放，AI 不知道从哪开始 | 给 Layer 1 summary + 明确任务 |
| 让 AI 列出所有 Draw Call | 5K+ token，AI 看不懂 | 给 Top 10 + 异常标记 |
| 让 AI 自己"看看 shader 哪里可以优化" | 范围太广 | 给具体 shader 统计 + 资源使用情况 |
| 把纹理贴给 AI | 1 张图 = 1K+ token | 给纹理元数据 + 采样密度 |
| 一次性问 5 个 Pass | 注意力分散 | 一次只分析 1-2 个 suspect |

---

## 12. 完整工作流示例

```bash
# 1. 工程师截一帧 → 跑预处理脚本
python preprocess.py capture.rdc > layer1.json
python preprocess.py capture.rdc --pass "Lighting,UI" > layer2.json

# 2. 给 AI 的 prompt
"""
分析以下渲染性能数据：

<layer1.json>
<layer2.json>

按指定格式输出瓶颈诊断 + 优化建议（<600 tokens）。
"""

# 3. 如果需要下钻
python preprocess.py capture.rdc --shader PSO_Char_Skin --stats > layer3.json

# 4. 再次问 AI（针对性下钻）
"""
基于上轮分析，进一步下钻 PSO_Char_Skin：

<layer3.json>

确认是否 PS 指令过多，给出具体优化方向。
"""
```

**总 token**：2 次调用，每次 < 2K，总 < 4K，**20x 比"全量分析"更省**。

---

## 一句话总结

> **AI 看 RenderDoc 的本质是"漏斗化数据流"**：脚本统计 → 摘要 → AI 推理 → 假设 → 精准下钻 → 早停。
> 不要让 AI 漫游，让它**做判断题**而不是**做阅读理解**。
