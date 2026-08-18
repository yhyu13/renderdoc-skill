# renderdoc-skill

一个 [Claude Code](https://docs.anthropic.com/en/docs/claude-code) 技能，让 Claude 具备使用 [RenderDoc](https://renderdoc.org) 捕获、检查、调试 GPU 帧的能力。支持 Vulkan、D3D11、D3D12、OpenGL，以及 **WebGPU（通过 Chrome 的 D3D12 后端进程注入）**。

## 演示

[![观看演示](https://img.youtube.com/vi/UkaXPtCWwo4/maxresdefault.jpg)](https://www.youtube.com/watch?v=UkaXPtCWwo4)

## 功能

当你描述一个渲染问题——阴影错误、颜色不对、物体缺失、性能问题——Claude 可以：

- **捕获 GPU 帧**：通过 RenderDoc 的 Python API 从你的应用捕获
- **检查管线状态**：任意 draw call 的着色器、混合、深度、光栅化、绑定
- **导出并查看渲染目标**为 PNG（Claude 是多模态的，能“看到”你的帧缓冲）
- **逐行调试着色器**：跟踪像素/顶点/计算着色器的执行
- **跟踪像素历史**：找出哪个 draw 写入了某个颜色及原因
- **编辑并重放着色器**：无需重新编译你的应用
- **比较帧**：并排对比以发现回归
- **捕获 WebGPU**：通过 Chrome D3D12 后端进程注入，捕获 three.js `WebGPURenderer`（如 `12_ddgi`）

它通过 [`rdc-cli`](https://github.com/BANANASJIM/rdc-cli) 工作——一个把 RenderDoc Python API 包装成 shell 命令的 66 命令 CLI，供 Claude Code 调用。

## 环境要求

| 要求 | 说明 |
|------|------|
| [RenderDoc](https://renderdoc.org) | 需要 `renderdoc.pyd` + `renderdoc.dll`（来自 RenderDoc 安装包或自行构建） |
| Python 3.10+ | 需与 `renderdoc.pyd` 构建所用的 Python 版本一致 |
| [rdc-cli](https://github.com/BANANASJIM/rdc-cli) | `pip install rdc-cli` |
| [Claude Code](https://docs.anthropic.com/en/docs/claude-code) | Anthropic 的 CLI agent |

## 安装

### 1. 克隆仓库到你的项目

```bash
# 方式 A：克隆进你的项目目录
cd /path/to/your/project
git clone https://github.com/rudybear/renderdoc-skill .claude/skills/renderdoc-gpu-debug

# 方式 B：独立克隆后拷贝技能文件
git clone https://github.com/rudybear/renderdoc-skill
cp -r renderdoc-skill/.claude/skills/renderdoc-gpu-debug /path/to/your/project/.claude/skills/
```

### 2. 安装 rdc-cli

```bash
pip install rdc-cli
```

### 3. 设置 RenderDoc 模块路径

把 `RENDERDOC_PYTHON_PATH` 指向包含 `renderdoc.pyd` 与 `renderdoc.dll` 的目录：

```bash
# 在你的 shell 配置里（.bashrc、.zshrc 等）
export RENDERDOC_PYTHON_PATH=/path/to/renderdoc/module
```

如果你用官方安装包安装 RenderDoc，通常是：
- **Windows**：`C:/Program Files/RenderDoc/`
- **Linux**：`/usr/lib/renderdoc/` 或你构建的位置

### 4. 注册 Vulkan layer（仅 Vulkan 应用）

Vulkan 捕获需要注册 RenderDoc 隐式 layer：

- **Windows**：把 `renderdoc.json` 加入 `HKCU\SOFTWARE\Khronos\Vulkan\ImplicitLayers`（DWORD 0）
- **Linux**：把 `renderdoc.json` 拷贝到 `~/.local/share/vulkan/implicit_layer.d/`

同时设置：`export ENABLE_VULKAN_RENDERDOC_CAPTURE=1`

### 5. 验证

```bash
rdc doctor
```

所有检查应全部通过。

### 6. 自定义 CLAUDE.md

编辑本仓库的 `CLAUDE.md`，填入你项目相关的路径（应用可执行文件、工作目录、捕获输出目录），告诉 Claude 你的具体环境。

## WebGPU / WebGL 捕获

RenderDoc **没有原生的 WebGPU 后端**——捕获 WebGPU 需要把 RenderDoc 注入到浏览器（Chrome）的 D3D12 后端；WebGL/WebGL2 则直接捕获。

- **WebGPU**（three.js `WebGPURenderer`，如 `12_ddgi`）：Chrome v144+（Canary）D3D12 进程注入。完整 flag 集合、Dawn 标签特性与注意事项见 [references/webgpu-capture.md](.claude/skills/renderdoc-gpu-debug/references/webgpu-capture.md)。用脚本自动化：

  ```bash
  python capture_webgpu.py --url http://localhost:5189 -o D:/renderdoc/captures/ddgi.rdc
  ```

  得到的 `.rdc` 就是一个普通的 D3D12 捕获，用常规 `rdc` 命令检查即可。具体检查方法见 [Recipe 7](.claude/skills/renderdoc-gpu-debug/references/debugging-recipes.md)。

- **WebGL**（`WebGLRenderer`）：直接用 `rdc capture -- /path/to/browser`，或 RenderDoc `File > Launch Application` 捕获。

详见 SKILL.md §2「WebGPU / WebGL (browser)」小节。

## MCP 服务器（另一种安装方式）

除了（或代替）技能，你也可以用 MCP 服务器——把 rdc-cli 注册成 Claude Code 的原生工具，出现在 `/mcp` 里。

### 1. 安装 MCP 依赖

```bash
pip install -r requirements-mcp.txt
```

### 2. 注册到 Claude Code

```bash
claude mcp add rdc-tools -- python D:/renderdoc/mcp_server/server.py
```

### 3. 验证

在 Claude Code 里运行 `/mcp`，应看到 `rdc-tools`，含 13 个工具、2 个资源、6 个 prompt。

### 可用工具

| 工具 | 命令 | 用途 |
|------|------|------|
| `rdc_session` | open, close, status | 会话生命周期 |
| `rdc_overview` | info, stats, passes, count, gpus | 打开后的初步概览 |
| `rdc_draws` | draws, draw | draw call 导航 |
| `rdc_events` | events, event | API 事件列表 |
| `rdc_pipeline` | pipeline, bindings | 管线状态检查 |
| `rdc_shader` | shader, shaders, search, shader-map | 着色器检查 |
| `rdc_export` | rt, texture, thumbnail, mesh, buffer | 可视化导出（内联图片 + 路径） |
| `rdc_pixel` | pixel, pick-pixel, debug pixel/vertex/thread | 像素调试 |
| `rdc_diff` | diff | 帧比较 |
| `rdc_resources` | resources, resource, usage, tex-stats | 资源检查 |
| `rdc_shader_edit` | shader-build/replace/restore/encodings | 编辑-重放 |
| `rdc_capture` | capture, attach, trigger, list, copy | 帧捕获 |
| `rdc_vfs` | ls, tree, cat | 虚拟文件系统 |
| `rdc_command` | 任意 rdc 命令 | 通用兜底 |

## 工作原理

### 技能触发

Claude Code 基于 YAML frontmatter 关键词加载技能。当你提到 GPU 调试、RenderDoc、着色器、渲染目标、管线状态、视觉故障、WebGPU、WebGL、Chrome、Dawn、three.js 等，Claude 就会激活本技能并获得完整的 `rdc-cli` 命令词汇表。

### 会话生命周期

每个检查会话遵循 open-work-close：

```bash
rdc open path/to/capture.rdc   # 加载捕获
# ... 检查命令 ...
rdc close                       # 释放 GPU 资源
```

### 可视化检查模式

Claude 能看图片。核心调试循环是：

1. **导出**渲染目标或纹理为 PNG（`rdc rt EID -o output.png`）
2. **查看**：用 Claude Code 的 Read 工具查看（多模态——Claude 能看到图像）
3. **关联**：与管线状态数据关联（`rdc pipeline`、`rdc shader`、`rdc bindings`）
4. **诊断**：定位问题并给出修复建议

### 内置调试 recipe

本技能包含 7 个现成的调试工作流：

1. **物体不可见** — 裁剪、深度、混合、顶点变换检查
2. **颜色不对** — 纹理绑定、常量、混合状态、着色器跟踪
3. **阴影有问题** — shadow map 导出、深度偏移、光照矩阵、PCF
4. **性能差** — draw 数量、资源大小、过度绘制、GPU 计数器
5. **两帧之间发生了什么变化** — 帧 diff + 可视化对比
6. **调试这个像素** — 像素历史、着色器跟踪、变量检查
7. **WebGPU DDGI 探针（12_ddgi）** — Chrome D3D12 捕获、blend dispatch、探针 atlas、`ddgi_rayData` 缓冲区

## 文件结构

```
.claude/skills/renderdoc-gpu-debug/
  SKILL.md                          # 主技能（由 Claude Code 加载）
  references/
    commands-quick-ref.md           # 全部 66 个 rdc-cli 命令及参数/选项
    debugging-recipes.md            # 7 个扩展调试工作流（含 12_ddgi）
    webgpu-capture.md               # WebGPU（Chrome D3D12）捕获流程与注意事项

CLAUDE.md                           # 项目上下文（按需自定义）
capture_frame.py                    # 示例：通过 RenderDoc Python API 捕获一帧
capture_webgpu.py                   # 示例：注入 Chrome GPU 进程捕获 WebGPU
```

## 使用示例

安装后，直接用自然语言和 Claude Code 对话即可：

```
> 我场景里的阴影有块状和 acne 伪影，能帮我调试吗？

> 捕获一帧，让我看看 shadow map 长什么样。

> 为什么这个球体渲染成黑色？它应该是红色的。

> 比较这两个捕获，告诉我哪里变了。

> 调试像素 (256, 300)——为什么它是透明的？
```

Claude 会调用 `rdc-cli` 命令、导出 PNG 进行可视化检查、查看管线状态并跟踪着色器执行来定位问题。

## 致谢

- **[rdc-cli](https://github.com/BANANASJIM/rdc-cli)** by Jim (BANANASJIM) — 让本技能成为可能的 66 命令 CLI。MIT 许可证。
- **[RenderDoc](https://renderdoc.org)** by Baldur Karlsson — 底层 GPU 调试器。MIT 许可证。
- **[Claude Code](https://docs.anthropic.com/en/docs/claude-code)** by Anthropic — 运行本技能的 AI 编码 agent。

## 许可证

MIT。见 [LICENSE](LICENSE)。
