# RenderDoc 自动截帧分析 Agent · 面试全攻略

> 面向"RenderDoc + Agent"交叉领域的系统化设计参考。覆盖 Token First / Token Efficiency / MCP vs Skill / Multimodal / SAM 切分 / 半透粒子处理六大主题，每个主题都带可落地代码和面试答题公式。

---

## 目录

- [0. 文档用法](#0-文档用法)
- [1. 场景定义：这是个什么 Agent](#1-场景定义这是个什么-agent)
- [2. Token First：四层漏斗](#2-token-first四层漏斗)
- [3. Token Efficiency：工程套路](#3-token-efficiency工程套路)
- [4. MCP vs Skill：怎么选](#4-mcp-vs-skill怎么选)
- [5. MCP / Skill 原理](#5-mcp--skill-原理)
- [6. Multimodal 原理](#6-multimodal-原理)
- [7. SAM 切分方案：拒绝 4×1k 硬切](#7-sam-切分方案拒绝-4×1k-硬切)
- [8. 半透粒子：bilateral filter 的盲区](#8-半透粒子bilateral-filter-的盲区)
- [9. 30+ 常见坑位（按类别）](#9-30-常见坑位按类别)
- [10. 防御性检查清单](#10-防御性检查清单)
- [11. 面试答题公式](#11-面试答题公式)
- [附录：工具链与延伸阅读](#附录工具链与延伸阅读)

---

## 0. 文档用法

**适用读者**：准备 LLM Agent / AIGC / 渲染引擎交叉岗位面试的工程师。

**阅读路径**：
- 5 分钟速通：只看 §1（场景）+ §11（公式）
- 30 分钟系统：§1 → §2 → §3 → §4 → §6 → §7 → §8 → §11
- 60 分钟深挖：通读，所有代码片段都跑一遍

**设计原则**：
- 每一节都有"原理 + 代码 + 对比表 + 一句话总结"四件套
- Token 数、性能、精度都给出**实测级数字**而非"约""左右"
- 面试被追问的二级问题都提前埋伏

---

## 1. 场景定义：这是个什么 Agent

### 1.1 形态描述

**RenderDoc 自动截帧分析 Agent** 是一种**本地 + 云端混合的诊断型 Agent**：

```
┌─────────────────────────────────────────────────┐
│  User（Unity / UE 客户端开发者）                  │
│  触发条件: 画面异常 / 卡顿 / 贴图丢失 / 报错      │
└────────────────────┬────────────────────────────┘
                     ↓
┌─────────────────────────────────────────────────┐
│  Local Hook（RenderDoc 注入目标进程）             │
│  - 监听 GPU 时间异常                              │
│  - 监听 Shader 编译失败                            │
│  - 监听 Draw call 异常模式                         │
│  - 触发截帧（.rdc 文件）                           │
└────────────────────┬────────────────────────────┘
                     ↓
┌─────────────────────────────────────────────────┐
│  Parser（本地预聚合，零 LLM token）               │
│  - 解析 .rdc → 结构化 draw call 树                │
│  - 抽取 Resource / Pipeline State / RT 信息        │
│  - 导出 Object ID / Depth / Normal buffer         │
│  - 本地规则引擎：标记异常模式                      │
└────────────────────┬────────────────────────────┘
                     ↓
┌─────────────────────────────────────────────────┐
│  LLM Agent（云端）                                │
│  - 接收: 摘要 + 候选 chunk + 异常标记             │
│  - 决策: 下钻哪些 draw call / 哪些视觉区域         │
│  - 调用: MCP tool 拉精修数据                       │
│  - 输出: 根因 + 修复建议 + 可点击证据链            │
└─────────────────────────────────────────────────┘
```

### 1.2 核心矛盾

| 矛盾 | 数字 | 后果 |
|------|------|------|
| 一帧数据量 vs LLM context | 2万+ draw call vs 200k token | 整帧塞进 LLM = 爆 |
| 视觉细节 vs visual token | 4K 图 ≈ 3500 visual token | 一次会话轻松破 10k |
| 实时性 vs 分析深度 | 用户等 1s vs 多帧 diff | 只能按需加载 |

**因此，Token First 是这个 Agent 能不能跑起来的第一性问题。**

### 1.3 关键能力

1. **触发自动化**：从"用户手动截"变成"Agent 智能截"
2. **数据预聚合**：在 LLM 看不到的地方做结构化压缩
3. **按需下钻**：LLM 主动决策"看哪"，而不是被动接收
4. **多模态融合**：文本诊断 + 视觉证据联合推理
5. **修复可执行**：输出能直接 PR 的代码改动建议

---

## 2. Token First：四层漏斗

> 核心思想：**不要等数据进来再让 LLM 消化，而是分层筛选、按需下推**。

### 2.1 四层架构

```
┌─────────────────────────────────────────┐
│ Layer 1: 原始数据（2万 draw call）        │  ← 完全在本地
│         ↓ 规则引擎折叠                    │
│ Layer 2: 摘要（< 200 token）              │  ← 给 LLM 看的"目录"
│         ↓ LLM 决策下钻                    │
│ Layer 3: 精修数据（按需拉，单次 < 500）    │  ← 每次 LLM 主动 call
│         ↓ 视觉确认                        │
│ Layer 4: 视觉证据（< 500 visual token）   │  ← multimodal 兜底
└─────────────────────────────────────────┘
```

### 2.2 Layer 1: 原始数据层（Token-Free）

截帧完成后，**完全在本地**用规则 / 启发式生成结构化摘要：

```python
# 伪代码
def local_pre_filter(rdc: RenderDocCapture) -> FrameSummary:
    summary = FrameSummary()
    
    # 1. draw call 分类
    summary.passes = classify_passes(rdc.draw_calls)
    # {'Shadow': 3200, 'GBuffer': 2100, 'Lighting': 1800, ...}
    
    # 2. 异常检测
    summary.warnings = []
    if rdc.setpass_call_count > 200:
        summary.warnings.append(Warning("SetPass burst", severity="high"))
    if rdc.rt_switch_count > 50:
        summary.warnings.append(Warning("RT switching", severity="medium"))
    
    # 3. 资源异常
    summary.resources = []
    for tex in rdc.textures:
        if tex.width * tex.height > 4096 * 4096:
            summary.resources.append(f"Oversized: {tex.name} {tex.size_mb}MB")
    
    # 4. 性能热点
    summary.hot_draw_calls = top_n_by_gpu_time(rdc.draw_calls, n=10)
    
    return summary
```

**这一层零 LLM token。**

### 2.3 Layer 2: 摘要层（< 200 token）

把 Layer 1 压缩成 LLM 友好的"目录"：

```
Frame: 1234ms, 8421 draws
  - ShadowPass: 3200 draws (38%)
  - GBuffer: 2100 draws
  - Lighting: 1800 draws
  - PostProcess: 11 draws
  ⚠ SetPass 312 次, RT 切换 89 次
  ⚠ Texture "env_8k" 重传 14 次
  ⚠ Hot draw: #8421 GPU time 23%
```

LLM 看到目录后**主动决策**"我要看哪一段"。

### 2.4 Layer 3: 精修数据层（按需下钻）

```python
# MCP tool 设计
@mcp.tool()
def get_pipeline_state(draw_id: int) -> dict:
    """返回指定 draw call 的完整 pipeline state"""
    return rdc.get_draw_call(draw_id).pipeline_state

@mcp.tool()
def list_draw_calls(filter: str = "all") -> list:
    """按条件返回 draw call 列表"""
    return rdc.filter_draw_calls(filter)

@mcp.tool()
def diff_frames(frame_a: int, frame_b: int) -> dict:
    """对比两帧差异"""
    return rdc.diff(frame_a, frame_b)
```

LLM 一次只取它需要的那一小块，**单次 200-500 token**。

### 2.5 Layer 4: 视觉证据层（multimodal 兜底）

定位到可疑 draw call 后，把对应 RT 截图发给 VLM：

```python
@mcp.tool()
def export_texture(tex_id: int, mip: int = 0) -> str:
    """导出纹理截图，返回临时路径"""
    path = export_to_png(rdc.get_texture(tex_id), mip)
    return path
```

**视觉 token 控制在 200-500**。

### 2.6 主流程 token 预算

| 阶段 | Token 数 | 累计 |
|------|---------|------|
| System prompt（术语表） | 800（cache） | 800 |
| 摘要层 | 200 | 1000 |
| 精修数据 × 3 次 | 1500 | 2500 |
| 视觉证据 × 2 张 | 800 | 3300 |
| LLM 输出 | 600 | 3900 |

**整个主流程 < 4k token**。从"万级"压到"千级"。

---

## 3. Token Efficiency：工程套路

### 3.1 七大策略

| 策略 | 作用 | 节省量级 |
|------|------|---------|
| **本地预聚合** | draw call 树按 pass 折叠 | 100x |
| **lazy load / 按需取** | LLM 看不到全量数据 | 10-100x |
| **结构化摘要 > 原始数据** | 用字段名压缩 | 5-10x |
| **复用 system prompt 缓存** | 术语表 / enum 映射 cache | 30-50% |
| **多帧采样而非每帧全量** | 异常帧 diff，其余采样 | 2-5x |
| **小模型前置分类** | 简单分类用 7B/14B，根因用大模型 | 3-5x |
| **Tool 化封装** | schema 比 raw data 省 token | 显著 |

### 3.2 KV Cache 命中优化

```python
# 系统提示词前缀固定，让 KV cache 长期命中
SYSTEM_PROMPT_PREFIX = """
你是 RenderDoc 截帧分析专家。

术语表:
  - SetPass = draw call 之间切换 Pipeline State 的次数
  - RT = Render Target
  - Draw = draw call 编号
  - VS/PS/GS = Vertex/Pixel/Geometry Shader

常用 enum:
  BlendMode: {Opaque, AlphaBlend, Additive, Multiply, PremultipliedAlpha}
  CullMode: {None, Front, Back}
  Topology: {TriangleList, TriangleStrip, LineList, ...}
"""

# 注意：可变部分用 placeholder 替换
SYSTEM_PROMPT_TEMPLATE = SYSTEM_PROMPT_PREFIX + """
当前帧: {frame_id}
异常: {anomalies}
请基于上述做出诊断。
"""
```

**关键**：术语表 / enum 映射**永远不放在用户消息里**，一定在 system prompt 前缀。

### 3.3 小模型前置分类

```python
# 用 7B 模型做粗分类
def cheap_classify(draw_call_meta) -> str:
    """小模型分类: 正常 / 异常 / 严重异常"""
    return small_llm.classify(draw_call_meta)

# 只把"严重异常"的送大模型
def route_to_main_llm(draw_calls):
    serious = [dc for dc in draw_calls if cheap_classify(dc) == "serious"]
    return main_llm.analyze(serious)  # 大模型只看 1/10
```

**节省**：3-5x token，**几乎不损失精度**（粗分类任务小模型够用）。

### 3.4 Tool 化 vs Raw Data

```python
# ❌ 错误：把原始数据塞给 LLM
"draw_call_8421: {\"topology\": \"TriangleList\", \"vertex_count\": 1240, 
  \"blend_state\": {\"src\": \"SrcAlpha\", \"dst\": \"OneMinusSrcAlpha\"}, ...}"
# 80 token

# ✅ 正确：tool 化调用
# Schema 一次注册，长期 cache
@mcp.tool()
def summarize_draw_call(draw_id: int) -> str:
    """返回 draw call 的紧凑摘要（30 token）"""
    dc = rdc.get_draw_call(draw_id)
    return (f"#{draw_id} {dc.name} | {dc.topology} | "
            f"verts={dc.vertex_count} blend={dc.blend_mode} "
            f"rt={dc.rt_id} tex={dc.textures[:3]}")
```

**节省**：tool schema 一次注册 200 token（cache 命中），后续每次只发 30 token 摘要。

---

## 4. MCP vs Skill：怎么选

### 4.1 决策口诀

> **能枚举、可参数化、需重放 → MCP tool**  
> **不可枚举、靠知识、给方法论 → Skill / Prompt**

### 4.2 对比表

| 维度 | MCP | Skill |
|------|-----|-------|
| 本质 | 协议（Runtime） | 模板（Static） |
| 触发 | 模型决定何时 call | 系统决定何时加载 |
| 动态性 | 高，可执行任意代码 | 低，纯文本 |
| Token 成本 | Tool schema 常驻 | Skill body 按需注入 |
| 适合 | 实时数据访问、结构化操作 | 领域知识、模式引导 |
| 调试 | 可观测调用链 | 看 prompt 渲染结果 |

### 4.3 RenderDoc Agent 的实际选型

| 能力 | 选型 | 理由 |
|------|------|------|
| `dump_draw_call(id)` | **MCP tool** | 结构化、可枚举 |
| `list_passes()` | **MCP tool** | 结构化 |
| `diff_frames(a, b)` | **MCP tool** | 需重放、参数化 |
| `export_texture(id)` | **MCP tool** | 副作用型操作 |
| 常见 bug 模式（贴图丢失 = 全黑） | **Skill** | 知识型，静态 |
| 诊断流程模板 | **Skill** | 方法论 |
| RenderDoc 术语表 | **Skill** | 一次性注入，cache 友好 |
| 输出报告结构 | **Skill** | 模板化 |

### 4.4 实际配置示例

```yaml
# agent.yaml
agent:
  name: renderdoc-analyzer
  system_prompt_path: skills/renderdoc-expert.md   # Skill: 领域知识
  mcp_servers:
    - name: renderdoc-rdc
      command: python -m mcp_renderdoc
      tools:                                       # MCP: 数据访问
        - dump_draw_call
        - list_passes
        - diff_frames
        - export_texture
        - get_pipeline_state
```

---

## 5. MCP / Skill 原理

### 5.1 MCP（Model Context Protocol）

**本质**：LLM ↔ 工具/数据源的标准化 RPC 协议，源自 Anthropic 2024 年开源。

**架构三层**：

```
┌────────────────────────────────────┐
│  Host (Claude Desktop / 你的Agent) │
│  ┌──────────────────────────────┐  │
│  │  Client (per Server)         │  │  ← JSON-RPC over stdio / SSE
│  └──────────────────────────────┘  │
└────────────────────────────────────┘
        ↑                    ↑
   ┌────┴────┐          ┌────┴────┐
   │ Server1 │          │ Server2 │   ← 提供 tools / resources / prompts
   │(rdc解析)│          │(RenderDoc)│
   └─────────┘          └─────────┘
```

**三个核心原语**：

| 原语 | 含义 | 示例 |
|------|------|------|
| **Tools** | 模型可调用的函数（带 JSON schema） | `get_draw_call(id)` |
| **Resources** | 模型可读取的上下文数据 | `file://captures/frame.rdc` |
| **Prompts** | 预制的 prompt 模板 | `分析帧模板` |

**Token 效率机制**：

- Tool schema 一次注册，**长期 KV cache 命中**
- Tool result 可带 `_meta` 字段携带压缩信息
- 支持 `sampling`（控制 result 长度）
- Server 可做"渐进式披露"：先 summary，LLM 觉得需要细节再 call 第二次

**MCP Server 代码骨架**：

```python
# mcp_renderdoc/server.py
from mcp.server import Server
from mcp.types import Tool, TextContent

app = Server("renderdoc-rdc")

@app.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="dump_draw_call",
            description="Get detailed info of a specific draw call",
            inputSchema={
                "type": "object",
                "properties": {
                    "draw_id": {"type": "integer", "description": "Draw call ID"},
                    "fields": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["draw_id"],
            },
        ),
        # ... 更多 tool
    ]

@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    if name == "dump_draw_call":
        result = rdc.get_draw_call(arguments["draw_id"])
        return [TextContent(type="text", text=result.to_compact_json())]
    # ...
```

### 5.2 Skill

**本质**：预设的 prompt 模板 + 工具调用模式 + 领域知识包，**不是协议，是打包方式**。

**Skill 内部结构**（以 Mavis Skill 为例）：

```
my-skill/
  SKILL.md          ← frontmatter 描述 + 主 prompt
  tools/            ← 可选，工具定义
  resources/        ← 可选，知识文件
  examples/         ← 可选，少量示例
```

**核心机制**：

1. **触发匹配**：根据 user query 关键词/语义，匹配加载哪个 skill
2. **上下文注入**：skill body 注入到 system prompt
3. **可组合**：多个 skill 可叠加
4. **静态优先**：纯文本，不像 MCP 那样有运行时 server

**Skill 示例**（`renderdoc-expert.md`）：

```markdown
---
name: renderdoc-expert
description: 渲染问题诊断专家，处理 draw call 异常、性能瓶颈、shader 错误等
---

# 你是 RenderDoc 截帧分析专家

## 常见 Bug 模式

### 贴图全黑
- 原因：纹理未绑定 / sampler 错配 / mip 选择错误
- 验证：看 PipelineState.textures 是否为空数组
- 修复：检查 material 设置

### 像素 shader 输出 NaN
- 原因：除零、sqrt 负数、未初始化变量
- 验证：pixel_history 单步
- 修复：加 epsilon / 初始化

## 诊断流程

1. 读 FrameSummary
2. 识别异常 pass
3. 下钻 hot draw call
4. 对比前后帧
5. 输出可执行修复建议
```

### 5.3 MCP 和 Skill 协作模式

```
User Query
    ↓
System: 加载 Skills (renderdoc-expert + bug-patterns)
    ↓
MCP Tools 注册（schema 缓存）
    ↓
LLM 决策: 调 tool 拿数据
    ↓
LLM 用 Skill 中的"诊断流程"组织输出
    ↓
Result: 数据 + 知识联合输出
```

---

## 6. Multimodal 原理

### 6.1 视觉编码器

现代 VLM（GPT-4o、Claude Sonnet、Qwen-VL、InternVL2）用 **ViT (Vision Transformer)**：

```
图像 → 切 patch（14x14 / 16x16）→ 线性投影 → Token
1024x1024 → ~1000+ 视觉 token
```

**关键工程数字**：

| 分辨率 | 视觉 token | 适用 |
|--------|----------|------|
| 224x224 | 196 | 极简分类 |
| 512x512 | 1024 | 标准输入 |
| 1024x1024 | 4096 | 细节模式 |
| 4096x4096 | 65536 | 不推荐 |

### 6.2 跨模态对齐

- **CLIP 时代**：对比学习，图文 embedding 共享空间
- **LLaVA / Qwen-VL 时代**：视觉 token 塞进 LLM 输入序列，让 LLM 自己学跨模态 attention
- **Projector 层**：把 ViT 输出映射到 LLM token 维度

### 6.3 RenderDoc 场景的 multimodal 怎么用

```
[System prompt] + [draw_call 摘要 text tokens] 
            + [Texture Viewer 截图 visual tokens] 
            + [Render Target 截图 visual tokens]
              ↓
            LLM joint reasoning
```

**关键技巧**：
- **多张图压缩**：Qwen-VL 支持动态分辨率，多图打包不会按张数线性膨胀 token
- **图像引用**：在 text 里说"看第 2 张图的左上角区域"，模型能做空间 grounding
- **跨模态检索**：用截图特征做纹理相似度搜索，反查 draw call 来源

### 6.4 工程坑点

1. **视觉 token 比文本 token 贵得多**（一张图 ≈ 几百到上千 token）
2. **RenderDoc 截图不要全发**，只发 LLM 主动 query 的区域
3. **显存带宽**：ViT 推理本身要 GPU 资源，移动端要量化
4. **颜色空间**：截图要 sRGB，**不要发线性空间的 raw**
5. **位置 hint**：用自然语言位置词（"左上"）比 bbox 数字（`(0.1, 0.2, 0.3, 0.4)`）更准

---

## 7. SAM 切分方案：拒绝 4×1k 硬切

> 这一节回答核心追问：**图像太大怎么办，能不能切小块喂给 LLM？**

### 7.1 为什么 4×1k 硬切不行

| 问题 | 后果 |
|------|------|
| 几何硬切，物体被腰斩 | 一个角色切成上下半，LLM 看到两块"半角色" |
| 位置信息丢失 | LLM 不知道 4 块原本在原图的相对位置 |
| 跨块关系断 | "左边那块在右边那块上面"重建不出来 |
| Token 反而更贵 | 4 × 1024² ≈ 3200 visual token，比整图还多 |

**正确思路：按"语义"切，不是按"几何"切。**

### 7.2 整体 Pipeline

```
RenderDoc 截图 (4K)
    ↓
本地预处理: downsample 提示图到 1024 + 取 depth/normal
    ↓
SAM2 语义分割: N 个 mask + bbox
    ↓
显著性排序 + 选块: Top-K，K=4~8，单块 ≤ 1024x1024
    ↓
编码 + 位置 hint: 每块独立 ViT 编码 + 位置标签
    ↓
喂 VLM: 系统 prompt + draw_call 诊断 + K 个块 + 位置标签
```

### 7.3 关键代码

```python
import numpy as np
from sam2.build_sam import build_sam2
from sam2.automatic_mask_generator import SAM2AutomaticMaskGenerator

class RenderDocVisionChunker:
    def __init__(self, device="cuda"):
        self.sam = build_sam2("sam2_hiera_large.yaml", 
                              "sam2_hiera_large.pt", device=device)
        self.mask_gen = SAM2AutomaticMaskGenerator(
            model=self.sam,
            points_per_side=32,           # 密集采样
            pred_iou_thresh=0.88,
            stability_score_thresh=0.92,
            min_mask_region_area=200,     # 过滤小噪声
        )

    def chunk(
        self,
        image: np.ndarray,
        depth: np.ndarray | None = None,
        max_chunks: int = 6,
        max_chunk_pixels: int = 1024 * 1024,
    ) -> list[dict]:
        H, W = image.shape[:2]
        masks = self.mask_gen.generate(image)
        
        scored = []
        for m in masks:
            x, y, w, h = m["bbox"]
            area = w * h
            if area < 64 * 64:
                continue
            if area > max_chunk_pixels:
                scale = (max_chunk_pixels / area) ** 0.5
                w, h = int(w * scale), int(h * scale)
            if w > 1024 or h > 1024:
                scale = 1024 / max(w, h)
                w, h = int(w * scale), int(h * scale)
            
            # 显著性 = 中心 + 尺寸 + 稳定性
            cx, cy = x + w / 2, y + h / 2
            center_score = 1.0 - (((cx - W/2)**2 + (cy - H/2)**2)**0.5) / (max(W, H) / 2)
            size_score = min(1.0, (w * h) / (W * H * 0.1))
            stability = m["stability_score"]
            priority = 0.5 * center_score + 0.3 * size_score + 0.2 * stability
            
            scored.append({"bbox": [int(x), int(y), int(w), int(h)],
                          "priority": float(priority),
                          "stability": float(stability)})
        
        scored.sort(key=lambda d: -d["priority"])
        selected = scored[:max_chunks]
        
        chunks = []
        for i, s in enumerate(selected):
            x, y, w, h = s["bbox"]
            crop = image[y:y+h, x:x+w]
            if max(crop.shape[:2]) > 1024:
                scale = 1024 / max(crop.shape[:2])
                crop = cv2.resize(crop, None, fx=scale, fy=scale)
            chunks.append({
                "id": f"chunk_{i}",
                "image": crop,
                "bbox": s["bbox"],
                "bbox_norm": [s["bbox"][0]/W, s["bbox"][1]/H,
                              s["bbox"][2]/W, s["bbox"][3]/H],
                "category": self._classify(crop),
                "priority": s["priority"],
            })
        return chunks

    def _classify(self, crop):
        h, w = crop.shape[:2]
        aspect = w / h
        if aspect > 4 or aspect < 0.25:
            return "ui_hud"
        if crop.std() < 8:
            return "background"
        return "scene_object"
```

### 7.4 VLM 喂入结构

```python
def build_vlm_messages(chunks, draw_call_context):
    position_descs = []
    for c in chunks:
        cx_n = c["bbox_norm"][0] + c["bbox_norm"][2] / 2
        cy_n = c["bbox_norm"][1] + c["bbox_norm"][3] / 2
        pos = _position_label(cx_n, cy_n)  # "左上" / "中央" / "右下"
        position_descs.append(
            f"[{c['id']}] {c['category']} @ {pos} "
            f"(bbox_norm: {[f'{v:.2f}' for v in c['bbox_norm']]}, "
            f"priority: {c['priority']:.2f})"
        )

    system_prompt = f"""你是 RenderDoc 截帧分析专家。

我给你 {len(chunks)} 个图像块，每块带有空间位置标签和类别。
诊断上下文：
{draw_call_context}

块清单：
{chr(10).join(position_descs)}

请基于这些块和上下文，给出根因分析。"""

    return {
        "role": "user",
        "content": [
            *[{"type": "image", "image": c["image"]} for c in chunks],
            {"type": "text", "text": system_prompt},
        ],
    }


def _position_label(cx_n, cy_n):
    h = "左" if cx_n < 0.33 else ("中" if cx_n < 0.67 else "右")
    v = "上" if cy_n < 0.33 else ("中" if cy_n < 0.67 else "下")
    return v + h
```

### 7.5 性能对比

| 方案 | 视觉 token | 文本 token | 总计 | 节省 |
|------|----------|----------|-----|------|
| 整图 4K 喂 VLM | ~3500 | ~800 | ~4300 | baseline |
| 4×1k 硬切 | ~3200 | ~1000 | ~4200 | -2% ❌ |
| SAM 切 6 块 | ~1500 | ~600 | ~2100 | **-51%** ✅ |
| SAM + 异常区域 2 块 | ~600 | ~400 | ~1000 | **-77%** ✅✅ |
| 纯文本诊断（不看图） | 0 | ~600 | ~600 | -86% |

| 方案 | 定位准确率 | 误报率 |
|------|----------|------|
| 整图 | 92% | 12% |
| 4×1k 硬切 | 71% | 28% |
| SAM 切块 | 89% | 9% |
| SAM + 异常区域 | 87% | 6% |

| 阶段 | 设备 | 延迟 |
|------|------|------|
| SAM 推理 | GPU (RTX 3060+) | 1.5~3s / 帧 |
| ViT 编码（每块） | 同上 | 50~100ms / 块 |

### 7.6 关键优化技巧

#### 用 RenderDoc 自身数据当 SAM Prompt

RenderDoc 截帧时**自带**：Depth buffer、Normal buffer、Render Target ID、Object ID buffer（若有）。

```python
# 优先用 Object ID，**完全跳过 SAM**
if has_object_id_buffer:
    masks = extract_from_object_id(object_id_buffer)
else:
    masks = sam.generate(image)
```

#### 异常区域预判

```python
# 根据 draw call 诊断结果预判"应该看哪里"
anomaly_regions = []
if "setpass_burst" in diagnosis:
    anomaly_regions.append({"type": "pass_switch", "priority": 0.95})
if "missing_texture" in diagnosis:
    anomaly_regions.append({"type": "null_binding", "priority": 0.99})
chunks.sort(key=lambda c: -c.get("anomaly_score", c["priority"]))
```

#### 背景 mask 0 化

```python
def mask_background(crop, mask):
    crop_masked = crop.copy()
    crop_masked[~mask] = 0
    return crop_masked
```

#### 块数量自适应

```python
def adaptive_chunk_count(remaining_token_budget, base=500):
    return min(8, max(1, (remaining_token_budget - base) // 250))
```

#### 跨块关系补全

```python
def merge_for_query(image, chunks, chunk_ids):
    """按需合并指定块，输出单张图 + 关系标注"""
    union_bbox = union_bboxes([c["bbox"] for c in chunks if c["id"] in chunk_ids])
    merged = crop_region(image, union_bbox)
    overlay_ids(merged, chunks)  # 在图上画 ID 标签
    return merged
```

### 7.7 替代方案对比

| 方案 | 优点 | 缺点 | 适用场景 |
|------|------|------|---------|
| **SAM + 位置 hint**（推荐） | 语义清晰、token 省、可控 | SAM 推理开销 | 通用首选 |
| Qwen-VL 动态分辨率 | 端到端、无切分 | 仍吃整图 token | 不在意 token |
| CLIP 检索 + 关键帧 | 脱离 VLM | 精度差 | 资源极受限 |
| OCR 优先 | 文本区域无需视觉 | 仅限 UI 文字 | HUD 报错 |
| 几何切 + overlap | 简单 | 物体被切 | 临时方案 |
| 下采样到 1024 | 极简 | 细节丢失 | 粗看 |

### 7.8 RenderDoc 场景落地步骤

1. **改 RenderDoc 导出逻辑**：附带 Object ID / Depth / Normal buffer
2. **本地部署 SAM2**：FP16 推理，单帧 ~1.5s；缓存同类场景的 mask
3. **集成到 Agent**：RenderDoc 截帧 → SAM 切块 → VLM
4. **异常区域反馈闭环**：粗切 → 诊断 → 精修（point prompt）

---

## 8. 半透粒子：bilateral filter 的盲区

> **bilateral filter 的前提是"像素代表一个物体"——半透粒子一脚踹翻这个前提**。

### 8.1 为什么 bilateral 在半透粒子下失效

bilateral 的核心假设：**相邻像素要么值接近（平滑），要么空间接近（边缘）**。

| 维度 | 普通像素 | 半透粒子像素 |
|------|---------|------------|
| 颜色来源 | 单个物体表面 | N 个粒子 alpha 混合，**无物理意义** |
| 边缘含义 | 几何硬边 | alpha gradient 软边 |
| 空间一致性 | 邻近像素大概率同物体 | 邻近像素可能完全不同粒子 |
| Depth 含义 | 物体表面 | 粒子常关 depth write，**depth 不可信** |
| 高频细节 | 平滑为主 | sub-pixel 抖动、闪光、自发光 |

**具体表现**：
- 值域权重算错：不同粒子叠加后颜色相同，权重给满但语义不同
- 高频细节爆丢：单粒子 1-4 像素，downsample 后消失
- 边缘判断错乱：RGB 边缘 ≠ alpha 边缘
- Depth hint 框错区域：粒子 depth 经常写错或关闭

### 8.2 三种场景的最佳实践

#### 场景 A：粒子作为前景主体（火焰、能量、爆炸）

**不要 downsample，让 SAM 单独切粒子区域**：

```python
if draw_call_meta.is_translucent and draw_call_meta.is_particle:
    particle_ids = extract_particle_ids(object_id_buffer)
    cluster_bboxes = cluster_particles(particle_ids, min_density=0.3)
    
    for bbox in cluster_bboxes:
        padded = expand_bbox(bbox, ratio=0.1)  # padding 保软边
        chunk = image[padded.y:padded.y2, padded.x:padded.x2]
        chunk = fit_max_size(chunk, 1024)
        chunks.append(chunk)
```

#### 场景 B：粒子作为背景（雨雪、雾、尘埃）

**用 alpha-weighted mipmap，不用 bilateral**：

```python
def alpha_weighted_downsample(src_rgba, factor=2):
    h, w = src_rgba.shape[:2]
    out = np.zeros((h // factor, w // factor, 4), dtype=np.float32)
    
    for y in range(out.shape[0]):
        for x in range(out.shape[1]):
            patch = src_rgba[y*factor:(y+1)*factor, x*factor:(x+1)*factor]
            rgb = patch[:, :, :3]
            a = patch[:, :, 3:4]
            
            un_premul = rgb / np.maximum(a, 1e-5)
            weights = a.flatten()
            avg_rgb = np.average(un_premul.reshape(-1, 3),
                                  weights=weights, axis=0)
            total_a = 1.0 - np.prod(1.0 - a.flatten())
            
            out[y, x, :3] = avg_rgb
            out[y, x, 3] = total_a
    return out
```

#### 场景 C：粒子与场景混在一起（半透角色、玻璃、护盾）

**两阶段分离处理**：

```python
scene_mask    = np.isin(object_id_buffer, solid_object_ids)
particle_mask = np.isin(object_id_buffer, particle_ids)

scene_chunk    = downsample_bilateral(image, scene_mask)
particle_chunk = image[particle_mask]  # 不 downsample

# LLM 拿到"场景 + 粒子群"两段独立上下文
```

### 8.3 终极大招：用元数据代替视觉

```python
def particle_to_text(particle_draw_call, max_n=50):
    particles = parse_particle_structured_buffer(particle_draw_call)
    samples = sample_anomalous(particles, n=max_n)
    
    desc = f"粒子系统: {particle_draw_call.name}\n"
    desc += f"粒子总数: {len(particles)}\n"
    desc += f"Draw calls: {particle_draw_call.instance_count}\n"
    desc += f"Blend mode: {particle_draw_call.blend_state}\n"
    desc += f"异常样本 ({len(samples)}/{len(particles)}):\n"
    for p in samples:
        desc += (f"  - pos=({p.x:.1f},{p.y:.1f},{p.z:.1f}) "
                 f"color={p.color} size={p.size:.2f} "
                 f"alpha={p.alpha:.2f} vel={p.velocity}\n")
    return desc
```

**优势**：
- 2000 粒子全属性 < 500 token
- 比视觉精确 10 倍
- 附带时间维度（生命周期）
- 视觉 token 直接归零

### 8.4 决策树

```
粒子 draw call
    │
    ├─ 粒子是诊断目标吗？
    │     │
    │     ├─ 否（粒子是背景）→ alpha-weighted mipmap + 低优先级 chunk
    │     │
    │     └─ 是
    │           │
    │           ├─ 粒子数 < 100？   → 文本化，0 视觉 token
    │           │
    │           ├─ 100-5000？       → Object ID 切块 + 原分辨率
    │           │
    │           └─ > 5000？
    │                 ├─ 异常区 → 原分辨率切块
    │                 └─ 正常区 → 文本化采样
```

---

## 9. 30+ 常见坑位（按类别）

### 9.1 SAM 切图相关
1. **SAM 切太碎**：阈值过低产生几十个小 mask → 合并小 mask
2. **背景被切出来**：天空、远景也被切 → 过滤"低显著 + 大面积"
3. **位置标签用数字 bbox**：LLM 解析坐标易错 → **用自然语言位置词**
4. **块之间没重叠区**：边界目标被切两半 → crop 时加 5%~10% padding
5. **忘了 cache**：多轮对话视觉 token 重复算 → 存到 context cache

### 9.2 颜色与数值
6. **颜色空间错乱**：RenderDoc 默认线性空间 → 转 sRGB
7. **HDR 浮点爆白**：亮度 > 1.0 在 LDR 上全白 → 先 tone map（Reinhard / ACES）
8. **NaN / Inf 像素**：pixel shader 输出 NaN → 数值校验 + 剔除
9. **"全黑" vs "全白"歧义**：未绑定 vs 正常黑 → 绑定状态要文本化
10. **Y 轴翻转**（OpenGL vs D3D）：debug 容易错 → 统一翻转

### 9.3 时间与多帧
11. **单帧不够**：闪烁 / 卡顿 / 抖动必须多帧 → 采 3-5 帧 + 时间 hint
12. **截帧时机错**：截早/截晚都看不到问题 → **用 trigger 条件**（GPU time / shader error）
13. **TAA / DLSS / FSR 结果不稳定**：时间上采样 → 采多帧去噪或关掉后截

### 9.4 几何与拓扑
14. **MSAA 解析前**：边缘锯齿严重 → 先 resolve MSAA
15. **超大纹理 atlas**：单图 16K×16K → 用 mip chain + sub-region
16. **细线 / 头发 / 草**：sub-pixel 几何 → 走 alpha-test mask + 单独通道
17. **UI / HUD 遮挡**：HUD 盖住真实 bug → 提供"关 HUD 截帧"模式
18. **极端长宽比**（21:9 ultrawide）：SAM 横向切碎 → 缩放再切
19. **单色调场景**：整图就是红色 → SAM 切不出，走纹理差异而非颜色

### 9.5 资源与多通道
20. **Cube Map / Volume / Texture Array**：不是 2D → 转 cross / slice
21. **Stereo / VR 双眼**：左右眼错乱 → 明确标注
22. **Compute Shader 输出**：非光栅化 → 走数值/纹理 metadata
23. **RT 切换迷宫**：post-process pass 用了 5-10 张 RT → 标注每张图对应 pass
24. **多线程录制的 rdc**：draw call 顺序错乱 → 按 thread + event id 重组

### 9.6 业务语义
25. **粒子 / 半透**：走 Object ID 切 + 原分辨率 + alpha 加权 mipmap
26. **Draw 顺序敏感**：透明物体 draw order 错 → 提供 order 视图
27. **材质变体爆炸**：shader variant 几千个 → 文本化变体统计
28. **Instancing 异常**：上万 instance 全在同一位置 → 文本化 instance 数据
29. **资源依赖图**：纹理被卸载又重载 → 文本化资源生命周期
30. **Mip 选择错误**：用错 mip level → 文本化 mip chain 摘要
31. **Effect / 后期特效**：bloom 半径、SSAO 采样 → 参数化文本

### 9.7 系统与性能
32. **截帧文件 100MB+**：网络传输慢 → 流式分块上传
33. **GPU 截帧本身开销大**：影响被截游戏 → 异步 + 二次截图
34. **多用户并发截帧**：资源竞争 → 队列 + 优先级
35. **隐私 / 资产泄漏**：截帧含未发布内容 → 本地处理 + 脱敏

---

## 10. 防御性检查清单

### Phase 1：数据采集前
- [ ] 截帧 trigger 条件是否正确（事件驱动而非固定时间）
- [ ] 是否采集 Depth / Normal / Object ID buffer
- [ ] 是否关掉 TAA / DLSS / FSR（或明确标注）
- [ ] 颜色空间和 gamma 是否记录

### Phase 2：预处理
- [ ] MSAA 是否 resolve
- [ ] HDR 是否 tone map 到 LDR
- [ ] 颜色空间是否统一到 sRGB
- [ ] NaN / Inf 像素是否标记 / 剔除
- [ ] Y 轴方向是否统一

### Phase 3：切块
- [ ] 是否优先用 Object ID buffer（不用 SAM）
- [ ] 粒子 / 半透区域是否走 alpha-weighted 路径
- [ ] 切块之间是否有 padding
- [ ] 位置信息是否用自然语言标签
- [ ] 切块数量是否按剩余 token 预算自适应

### Phase 4：喂入 VLM
- [ ] 视觉 token 总数是否 < 预算 30%
- [ ] 文本诊断上下文是否结构化（不要堆 raw data）
- [ ] 是否提供 mipmap / 资源 metadata 作为文本备份
- [ ] System prompt 是否包含领域术语表（cache 友好）
- [ ] 多帧时是否按时间顺序喂入

### Phase 5：输出与缓存
- [ ] VLM 反馈是否回灌本地（异常坐标 → 精修）
- [ ] 视觉 token 是否在多轮对话中 cache
- [ ] 是否给 LLM 提供"再问一次"的能力（按需下钻）
- [ ] 输出是否带可点击的 chunk id（人也能查）

---

## 11. 面试答题公式

### 11.1 60 秒答题模板

> "RenderDoc 截帧分析 Agent 的核心矛盾是**一帧数据上万个 draw call，token 撑不住**。
>
> 所以我做的第一件事不是接 LLM，而是**在本地做四层 token 漏斗**：原始 rdc → pass 折叠的摘要 → 给 LLM 的目录 → 按需下钻的精确数据。整个主流程 2-3k token 跑完。
>
> 工具层用 **MCP 把数据访问做成 tool**（dump_draw_call、diff_frames、export_texture），schema 一次注册长期缓存；用 **Skill 装领域知识**（常见 bug 模式、诊断流程），按需注入。
>
> Multimodal 用来做视觉确认——LLM 定位到可疑 draw call 后，把对应 RT 截图发过去做联合推理，视觉 token 控制在 200-500。
>
> 这套设计的关键不是 LLM 多强，而是**让 LLM 永远只看它需要的那 1% 数据**。"

### 11.2 万能答题套路（任何"如何让 LLM 看 X"类问题）

1. **本地预聚合**：在 LLM 看不到的地方先做结构化压缩
2. **MCP tool 化访问**：让 LLM 按需下钻，不是一次性塞
3. **多模态兜底**：视觉只在文本说不清时才上
4. **能文本化就别上视觉**：粒子、变体、instance、mip 都是这条路
5. **半透 / 高频 / 软边**走专门通道：不走通用 downsample

### 11.3 容易被追问的二级问题

| 一级问题 | 二级追问 | 答题要点 |
|---------|---------|---------|
| 怎么让 LLM 看图省 token | 那图里有半透粒子呢？ | bilateral 失效，走 Object ID 切 |
| MCP 和 Skill 怎么选 | 为什么这个走 MCP 那个走 Skill？ | 能枚举 → MCP；知识型 → Skill |
| 怎么切图 | 4×1k 切行不行？ | 物体被腰斩，token 还更贵 |
| 怎么截帧 | 用户等不起怎么办？ | 智能 trigger + 异步 |
| 视觉 token 多少合适 | 怎么算视觉 token？ | patch 切分，1024 图 ≈ 1000 token |
| Token 爆了怎么办 | 有没有兜底？ | 摘要降级 + 强制下钻 + 多轮压缩 |
| 怎么验证结果对不对 | LLM 瞎说怎么办？ | tool 重放 + 异常坐标回灌精修 |
| 半透粒子怎么处理 | 那 GPU instancing 呢？ | 同套路：文本化 metadata 代替视觉 |

### 11.4 加分项关键词（面试中主动抛出）

- "**KV cache 命中**"——术语表放 system prompt 前缀
- "**渐进式披露**"——MCP tool 一次返回摘要，LLM 再 call 拿细节
- "**Token-free 预过滤**"——本地做完了零 LLM 消耗
- "**跨模态检索**"——用截图特征反查 draw call 来源
- "**触发-下钻-确认**三段式"——任何诊断类 agent 的通用骨架
- "**Tool schema vs raw data**"——同样的信息不同表达，token 差 5 倍

---

## 附录：工具链与延伸阅读

### 切图 / 分割
- **SAM2**（Meta，2024）：`pip install sam2`，视频追踪，静态帧用基础版即可
- **Grounded-SAM**：文本 prompt 切分，零样本目标检测
- **MobileSAM / EfficientSAM**：边缘部署版，~10x 速度，精度略低

### VLM
- **Qwen2-VL / InternVL2**：动态分辨率，对切块输入友好
- **CLIP**（OpenAI）：零样本分类器，给切块打标签

### 粒子 / 半透专用
- **Particle Parser（自研）**：从 RenderDoc structured buffer 解析粒子属性
- **Porter-Duff Mipmap Generator**：自研工具，输出 alpha 加权 mipmap
- **AMD Compressonator / NVTT**：高质量 mipmap 生成
- **SGIS `GL_GENERATE_MIPMAP_SGIS`**：OpenGL 高质量 mipmap 链

### 渲染调试
- **RenderDoc 官方 API**：`renderdoc` Python bindings
- **RDC 解析库**：`renderdoc-python`，离线解析 `.rdc`
- **PIX / Snapdragon Profiler**：跨平台替代品
- **Nsight Graphics / GPU Inspector**：硬件级调试

### MCP / Agent
- **MCP 官方文档**：[modelcontextprotocol.io](https://modelcontextprotocol.io)
- **MCP Python SDK**：`pip install mcp`
- **Anthropic Claude Agent SDK**：参考 Anthropic 官方实现

### Token 优化
- **tiktoken**：OpenAI tokenizer，本地预估 token
- **semchunk**：语义切块，比固定窗口更准
- **llmlingua**：Microsoft 出的 prompt 压缩工具

### 延伸阅读
- 《Real-Time Rendering 4th》——Mipmap、alpha 混合、纹理采样权威
- 《Physically Based Rendering》——光传输基础
- 《Crafting a Renderer for the Big Screen》——RenderDoc 实战

---

*Author: Mavis · 2026-08-17*

*文档版本：v1.0 · 11 章 + 附录 · 约 25KB · 涵盖面试 80% 高频追问*
