# RenderDoc 自动化调帧 + 修复 Harness 设计

> 灵感来源：Amazon AGI Lab *Perception Agent Harness — Annotation and Verification*
> (https://www.amazon.science/blog/introducing-the-perception-agent-harness-annotation-and-verification-open-source)

---

## 0. 一句话总结

把 **"Perception Agent 的标注 + 双层验证"** 范式平移到 **RenderDoc 自动截帧 + 渲染问题修复** 场景：
以 `.rdc` 抓帧为自包含 artifact，**在抓帧内部直接改 shader + replay 验证**，再由 agent 迭代修复直到通过确定性 + 行为双层检查。

---

## 1. 背景

### 1.1 Perception Agent 在解决什么

LLM / agent 改 UI 代码之后，**怎么知道改对了**？

Amazon 团队把这件事拆成两个独立原语：

| 原语 | 角色 | 输出 |
|---|---|---|
| **Nova Act Annotator**（标注） | 人 → Agent | 人在渲染好的页面上画圈/点/标，**输出结构化 artifact**（带 DOM target、computed style、坐标、语义） |
| **Nova Act Visual Verifier**（验证） | Agent 自检 | 改完代码后调用，分两层跑 check |
| **Harness 闭环** | 串联 | annotate → generate → verify → recover → report |

关键设计：

1. **结构化 artifact 取代自然语言反馈** —— 圈一下"这个 heading 改红"等价于一段带坐标 / 样式的对象，LLM 不会丢失上下文。
2. **双层验证，确定性优先**：
   - 第一层（确定性）：直接读 DOM `getComputedStyle`，校验颜色 / 圆角 / 偏移等纯数值。便宜、快、不调模型。
   - 第二层（行为）：按用户流走一遍（add → delete → checkout），看功能回归。
3. **Report 给人，不给人 autonomy** —— 最后的"这是 brand 选择"是工程师定的。
4. **Dogfood 闭环** —— annotator 插件本身是用 verifier 搭出来的。

### 1.2 我们面对的问题

引擎 / 客户端 / 工具开发里，**改了 shader / material / 渲染代码之后，怎么知道画面没坏 / 变好了**？

传统做法是：

```
改 shader 源码
  → 重新编译引擎 / 客户端（分钟级）
  → 启动游戏到对应场景
  → RenderDoc 抓帧
  → 人眼 / 像素比对
  → 不对 → 再来
```

每轮几分钟到几十分钟，迭代极慢。

---

## 2. 关键洞察：在 `.rdc` 内部闭环

RenderDoc 自带 **Shader Edit + Replay** 能力，可以做到：

- 打开一个 `.rdc` 文件；
- 对某个 draw call 的某个 stage（VS / PS / CS / ...）改 HLSL / GLSL 源码；
- RenderDoc 自动重编译到 DXBC / SPIR-V；
- Replay 那一帧，结果立刻在 Texture Viewer 里看到。

**整条 "改 → 编译 → 跑 → 看" 的循环全部在 `.rdc` 文件内部完成，不碰引擎、不重 build、不重启游戏**。

这正好对应 Perception Agent 的 "the rendered page is the artifact"。在渲染问题里，**`.rdc` 就是我们的"渲染好的页面"**，而且它比 web page 还彻底 —— 自包含、可 replay、可被脚本任意消费。

### 2.1 映射表

| Perception Agent（Web UI） | RenderDoc 调帧 |
|---|---|
| 渲染好的页面 | `.rdc` 抓帧 |
| Annotator 圈出来的"这块不对" | Capture 里的 draw call / pixel / shader 标记 |
| 确定性 CSS 检查 | 结构性检查：validation layer、pipeline state、resource binding、format / alignment / draw count |
| 行为流检查 | 像素历史、golden image 视觉比对、replay 帧 |
| Visual Verifier 报告 | "事件 342 的 CB 缺了 baseColor" 这种带事件 ID 的结构化报告 |
| Agent 改代码 | Agent 改 shader（在 `.rdc` 内） / 改源码（外部） |
| 工程师看 report 拍板 | TA / 工程师看对比报告决定接受 / 回滚 |

### 2.2 闭环循环（最小版）

```
.rdc (已抓好的"问题帧")
  ↓
① 标注 / 诊断：读 pipeline state + shader 反汇编 + 像素 diff → 结构化报告
  ↓
② 双层验证：
     L1 确定性 (validation layer / 资源绑定 / 数值约束)  ← 几乎零成本
     L2 行为     (像素比对 / 关键变量 / render target 哈希)
  ↓ fail
③ Agent 改 shader 源（HLSL / GLSL patch）  ← .rdc 内部，秒级
  ↓
④ Static check：shader 编译过、diff 不超范围
  ↓
⑤ Replay 同一帧
  ↓
回到 ②
  ↓ all pass
⑥ 导出最终 shader patch + 给出"建议应用回源码的 diff"
  ↓
人工 / CI 决定是否合入
```

**绝大多数纯 shader bug（颜色 / 光照 / blend / UV / 简单数学错）全在 .rdc 内部循环搞定**。需要改 pipeline state / descriptor / 资源创建的 bug，确定性层会标"必须 re-build 验证"，回到外部源码循环。

---

## 3. 双层验证架构（核心）

### 3.1 L1 确定性验证（.rdc 内部即可）

零模型成本，纯规则。能挡掉 80% 渲染 bug：

| 检查 | 工具 / API |
|---|---|
| Validation layer 报错 | `controller.GetDebugMessages()` |
| Pipeline state 对比 baseline | `controller.GetPipelineState(eventId)` |
| 资源绑定完整性 | `controller.GetBindlessResourceAccess(...)` |
| 数值约束（minLod ≤ mip count / 16-byte 对齐 / descriptor 数） | shader 反射 + 资源元数据 |
| Shader 反射关键变量范围 | `controller.GetShaderReflection(...)` |
| Draw call 数量 / 顺序异常 | `controller.GetDrawcalls()` |

**判定方式**：每条规则独立 pass / fail，输出带 `eventId`、`stage`、`resourceId`、`expected vs actual`。这是 agent 的"结构化 artifact"。

### 3.2 L2 行为验证

L1 没抓住、或需要语义理解才暴露的问题：

| 检查 | 工具 / API |
|---|---|
| 像素 diff vs golden | `controller.GetTextureData(rt, sub)` + 外部 diff 库 |
| 关键事件 render target 哈希 | 同上 |
| 像素历史追根 | `controller.PickPixel(rt, x, y)` + Enumerate pixel history |
| Shader 关键变量值 | 改 shader 加调试输出 → replay → 读 back |
| Overdraw heatmap | `Texture Viewer.Overdraw` 模式 |
| 反汇编关键字搜索 | `controller.GetDisassembly(target, shader)` |

**判定方式**：阈值化打分（像素差比例 / 关键区域 PSNR / 哈希一致）。失败时同样输出结构化报告 —— "事件 342、PS 阶段、shader MainPassPS、CBMaterial.baseColor 实际 (0,0,0,1)"。

### 3.3 验证顺序

**永远先跑 L1，全部 pass 才跑 L2**。理由：

- L1 几乎零成本，L2 可能要 replay + 读 RT，秒级。
- L1 失败的 bug 通常改不了 shader（pipeline state / 资源层），直接升级到"改源码 + 重新构建"路径，不在 .rdc 内瞎试。
- 节省 LLM token —— L1 失败是确定性事实，agent 不需要看；只有 L1 通过、L2 失败的"语义"问题才交给 LLM 理解。

---

## 4. 最小实现骨架

### 4.1 模块组成

```
renderdoc_harness/
├── capture/             # 触发抓帧（renderdoccmd / MCP）
├── diagnose/            # 把 .rdc 转成结构化诊断报告
│   ├── deterministic.py # L1 checks
│   └── behavioral.py    # L2 checks
├── agent/               # LLM 调用 + 改 shader
│   ├── patcher.py       # 生成 shader 补丁
│   └── static_check.py  # 编译 / 范围校验
├── replay/              # 注入 shader、重放帧、读回 RT
└── report/              # 输出 before/after 对比
```

### 4.2 核心 loop（伪代码）

```python
def iterate_shader_fix(rdc_path, target_event_id, original_hlsl, golden_fn, max_round=10):
    controller = load_capture(rdc_path)
    current_hlsl = original_hlsl
    history = []

    for round_i in range(max_round):
        # ③ 编译 shader → 字节码
        new_bytes = renderdoc_hlsl_to_dxbc(current_hlsl, stage=Pixel)

        # ④ static check（compile / syntactically valid）
        if not static_check(new_bytes):
            return {"status": "static_fail", "round": round_i, "source": current_hlsl}

        # ⑤ 注入 + replay
        controller.SetShaderBytes(target_event_id, renderdoc.ShaderStage.Pixel, new_bytes)
        controller.ReplayEvent(target_event_id, target_event_id)

        # ② 双层验证
        report = run_l1_deterministic(controller, baseline)  # 几乎零成本
        if not report.all_pass:
            # 确定性失败 → 升级到"改源码"路径
            return {"status": "needs_rebuild", "report": report, "round": round_i}

        score, l2 = run_l2_behavioral(controller, target_event_id, golden_fn)
        history.append({"round": round_i, "score": score, "l2": l2})

        if score < THRESHOLD:
            return {"status": "ok", "source": current_hlsl, "history": history}

        # 不够好 → LLM 改 shader
        current_hlsl = agent.patch_shader(
            original_hlsl,
            feedback=l2,                  # 结构化失败报告
            history=history,              # 之前几轮尝试
            constraints=SHADER_EDIT_RULES # 哪些变量不能动
        )

    return {"status": "exhausted", "history": history, "last": current_hlsl}
```

### 4.3 关键接口（RenderDoc Python）

| 用途 | API |
|---|---|
| 加载 .rdc | `pyrenderdoc.LoadCapture(path, ReplayOptions(), path, False, True)` |
| 取 controller | `pyrenderdoc.Replay().BlockInvoke(lambda c: setattr(global, "c", c))` |
| 注入 shader | `c.SetShaderBytes(eventId, renderdoc.ShaderStage.Pixel, new_bytes)` |
| Replay 区间 | `c.ReplayEvent(begin, end, renderdoc.ReplayFlags.Replay_AllDraws)` |
| 读 back RT | `c.GetTextureData(resourceId, subresource)` |
| Pipeline state | `c.GetPipelineState(eventId)` |
| Validation log | `c.GetDebugMessages()` |
| Shader 反射 | `c.GetShaderReflection(shaderId)` |
| 资源绑定 | `c.GetBindlessResourceAccess(...)` |
| 像素拾取 | `c.PickPixel(rtId, x, y)` |

> 完整 API 索引：https://renderdoc.org/docs/python_api/index.html

### 4.4 决策树

```
bug 报告进来
  │
  ├── 有现成 .rdc 吗？
  │     ├── 没有 → renderdoccmd 自动抓 → 回到循环
  │     └── 有 ↓
  │
  ├── 跑 L1 确定性 check
  │     ├── 失败（pipeline state / 资源错）→ ❌ 改源码 + re-build + 重新抓
  │     └── 通过 ↓
  │
  ├── 跑 L2 行为 check（golden diff）
  │     ├── 通过 → ✅ 是 golden 漂移，更新 baseline
  │     └── 失败 ↓
  │
  ├── .rdc 内 shader 改 + replay 循环（最多 N 轮）
  │     ├── 通过 → 输出 patch，建议应用回源码
  │     └── 耗尽 → 把历史报告给工程师 / TA 介入
  │
  └── 工程师拍板：接受 / 改 baseline / 改策略
```

---

## 5. 与现有工具的关系

不用从零造，下面这些已经覆盖了大半：

| 已有 | 提供什么 | 缺口 |
|---|---|---|
| `renderdoc-mcp` / `haolange/RDC-Agent-Tools` | MCP 工具（list_draws、get_pipeline_state、export_render_target、search_shaders 等 20+） | 缺"循环直到通过"的 orchestrator；缺确定性 / 行为分层 |
| Vulkan.org 教程（Goose + renderdoccmd） | 单次审计工作流范例 | 缺迭代 / 缺与源码联动 |
| `CGbull-46/Renderdoc-Debug-Agent` | 本地 MCP + Cloud Orchestrator + 前端面板原型 | 同样的"闭环"还没跑通 |
| RenderDoc 自身 | Shader Edit + Replay | 缺 agent 自动化 |

**真正缺的是一个"orchestrator + 双层验证 + 报告" glue**，把上面这些串成"失败 → 改 → 验证 → 改 → 通过"的自循环。

---

## 6. 落地建议

按这个顺序做，能最快见到收益：

### Phase 1 — 跑通单次诊断
- 用 `renderdoc-mcp` 串起来，验证 agent 能读 pipeline state、shader、RT。
- 输入：手抓的 .rdc + golden；输出：自然语言诊断。
- 价值：替代人肉点 GUI。

### Phase 2 — 加上 L1 确定性检查
- 把 validation layer / 资源绑定 / 数值约束写成 rule engine。
- 失败时直接给"必须改源码"的清单。
- 价值：把"看一眼就知道"的 bug 自动化。

### Phase 3 — `.rdc` 内 shader 改 + replay 循环
- 用 `controller.SetShaderBytes` + `controller.ReplayEvent` 实现秒级迭代。
- agent 给出 shader 源 patch，自动化编译 + 注入 + 重放。
- 价值：把"改 shader 试一下"从分钟级压到秒级。

### Phase 4 — L2 行为验证 + 报告
- 像素差 / 关键 RT 哈希 / 关键变量值。
- 输出 before/after 对比报告（带 patch diff、diff 截图、checklist 结果）。
- 价值：人只看报告就能拍板，不用再亲自点 RenderDoc。

### Phase 5 — CI 集成
- golden 漂移检测触发自动抓帧。
- 跑完 harness 给出 PR comment 或 Slack 通知。
- 价值：把"渲染回归"做成 PR gate。

---

## 7. 启发点小结（Perception Agent 给我们的设计原则）

| 原则 | 渲染场景里的落地 |
|---|---|
| 结构化 artifact > 自然语言 | shader patch / 诊断报告都是结构化 JSON，不是"看着不对" |
| 确定性检查先于模型 | validation layer / 数值约束先跑，挡掉 80% 问题 |
| 双层验证 (deterministic + behavioral) | L1 结构性 + L2 像素 / 关键变量 |
| Self-contained artifact | `.rdc` 是渲染的"rendered page"，自带世界 |
| Report 给人，autonomy 受限 | 最后的"接受 / 拒绝"永远是工程师 / TA 决定 |
| Dogfood 闭环 | harness 自己也是用 RenderDoc 搭出来的（renderdoc-mcp 调试 RenderDoc 自己） |

---

## 8. 开放问题

1. **Baseline 管理**：每次 shader / 引擎升级，golden 都要重抓。怎么做版本化 + 噪声过滤？
2. **Multi-pass bug**：一个 pass 的输出错了，会污染后续 pass 的输入。L2 检查能不能"反向定位"到第一个出错的 pass？
3. **CS（Compute Shader）怎么验**：不像 PS 有 render target 可看，CS 的输出是 buffer。是否要专门给 CS 一套 L2 规则？
4. **跨 API / 跨驱动**：同一份 baseline 在 NVIDIA / AMD / Intel 上行为可能不一样。harness 是跑单一平台还是多平台并行？
5. **Agent 改 shader 的"安全围栏"**：怎么防止 LLM 改坏无关变量？怎么自动定位 patch 影响范围？

---

## 9. 参考

- Amazon Science Blog: *Introducing the Perception Agent Harness — Annotation and Verification*
  https://www.amazon.science/blog/introducing-the-perception-agent-harness-annotation-and-verification-open-source
- Nova Act Annotator (Apache-2.0): https://github.com/amazon-agi-labs/nova-act-browser-extensions
- Nova Act Visual Verifier (Apache-2.0): https://github.com/amazon-agi-labs/nova-act-agent-skills
- RenderDoc Python API: https://renderdoc.org/docs/python_api/index.html
- AI × RenderDoc 工作流: https://docs.vulkan.org/tutorial/latest/AI_Assisted_Vulkan/06_debugging/03_renderdoc_ai_integration.html
- renderdoc-mcp / haolange/RDC-Agent-Tools
- Renderdoc-Debug-Agent (CGbull-46)
