# Stream OFFICE — 企业办公能力包（国内）

> 给 per-user 持久 agent 补足"日常办公占 70%"的工具面。本轮 = 文件处理能力（读写
> Excel/Word/PPT/PDF + 数据分析 + 中文）+ 办公 Skill；**国内协作连接器（钉钉/企微/
> 飞书）本轮暂缓**（受 MCP-client-only 方向 + 国内 remote MCP 生态约束）。

## 0. 来源与定位
- 触发：Stream TE 引擎层完成后，引擎能"承载"办公能力；用户拍板**服务国内客户、去国外连接器**（Gmail/Slack/Drive 不做）。
- 与 [[project_mcp_direction_client_only]] 一致：消费外部 MCP，不自造 server。
- 与 [[project_target_product_form]] 一致：per-user 持久 agent 的办公场景。

## 0.1 现状（已 file:line 核实，2026-06-05）
- 沙箱镜像 `infra/sandbox-image/Dockerfile` = `python:3.12-alpine`（~50MB）+ 纯 stdlib，**运行时卸载 pip**（安全 F-2/F-13）；无办公库、无中文字体、无 locale。
- 镜像名 supervisor **硬编码** `settings.sandbox_image`（`settings.py:34`，env `HELIX_SANDBOX_SANDBOX_IMAGE`）；`AcquireRequest`（`schemas.py:16`）无 image 字段；一个 supervisor 实例当前无法按请求选镜像。
- `SandboxSpec`（`agent_spec.py:225`）**已有未使用的 `image` / `image_build` 字段**；无 `image_variant`。
- `persistent_workspace`（J.15）是 manifest→supervisor 传递的**完美模板**：manifest → `agent_factory.build_tool_registry` → 沙箱工具字段 → `run_in_sandbox` → `acquire`。
- MCP catalog（Stream W）**只收 remote**（`sse`/`streamable_http`，`mcp_connector_catalog.py:37`），不收 stdio；钉钉/企微/飞书要进 catalog 必须有 HTTP MCP endpoint。
- Skill（Stream X）`SKILL.md` 打包就绪 + 中文 prompt-injection 扫描（`test_threat_patterns_chinese.py`）。

## 0.2 已锁决策（用户拍板，2026-06-05）
- **文件能力** = 纯 Python 库起步（slim + build-time pip）；libreoffice/pandoc 重型转换**推后**（512MB 内存撑不住 + gVisor 兼容风险）。
- **镜像** = 独立 office variant（minimal 保纯净给计算 agent；office 给办公），manifest `image_variant` 选。
- **国内连接器** = 本轮**暂缓**，记 backlog。

## 1. Mini-ADRs

### OFFICE-ADR-1 文件能力 = 纯 Python 库（slim 基础镜像）
- **决策**：office 镜像基于 `python:3.12-slim`（glibc → pandas/numpy/Pillow 有 manylinux wheels 直接装；alpine musl 需现编译）；`docker build` 阶段 `pip install` 办公库，**装完卸载 pip**（保运行时安全：仍无 pip、无 egress）。
- **库集**：`pandas` / `openpyxl`（xlsx）/ `python-docx`（docx）/ `python-pptx`（pptx）/ `pypdf` + `pdfplumber`（PDF 读）/ `Pillow`（图像）/ `matplotlib`（图表，中文需字体）。
- **不含**（推后）：libreoffice（.doc/.xls 老格式转换、Office→PDF 渲染）、pandoc、wkhtmltopdf。真有"格式互转"需求再开 OFFICE-1c。

### OFFICE-ADR-2 镜像 variant 机制（复用 persistent_workspace 链路）
- **决策**：`SandboxSpec` 加 `image_variant: Literal["minimal","office"] | None`（None→默认/minimal，向后兼容）。链路照搬 `persistent_workspace`：
  - `agent_factory` 读 `spec.spec.sandbox.image_variant` → `build_tool_registry(image_variant=...)` → 各沙箱工具（exec_python/bash/read_file/write_file/edit_file/list_dir）字段 → `run_in_sandbox(image_variant=...)` → `SupervisorClient.acquire(image_variant=...)`。
  - supervisor：`AcquireRequest` 加 `image_variant`；settings 加 `sandbox_image_office`；`_select_image(variant)` 选镜像名（未知/None → 默认 minimal）。
- **诚实约束**：variant 是 **agent/sandbox 级**（一个 agent 的所有沙箱工具同镜像），不是 per-tool；从 manifest 一处定。

### OFFICE-ADR-3 中文支持
- **决策**：office 镜像装 Noto CJK / 思源黑体 + `fontconfig` + 设 `LANG=zh_CN.UTF-8`（兼 UTF-8/GB18030 读写）。matplotlib 中文渲染配字体。验收：沙箱内生成含中文的 xlsx/docx/pptx/图表不乱码。

### OFFICE-ADR-4 国内连接器暂缓（不破 client-only）
- **决策**：钉钉/企微/飞书连接器**本轮不做**。国内官方 remote MCP server 生态薄，catalog 只收 remote；强上要么破 client-only 自搭 wrapper、要么仅单租户 on-prem stdio off-catalog。记 backlog，待生态成熟或单租户场景再开。helix 本就 backend-only、不内置末端 adapter。

### OFFICE-ADR-5 办公 Skill（SKILL.md 包，中文模板）
- **决策**：平台 Skill 打包常见办公流程（中文）：数据分析报告 / Excel 批处理 / Word 文档生成 / PPT 制作 / PDF 提取汇总。`tool_names` 声明用 `exec_python`（或 file 原语）；`lazy_load=true`；`required_tier` 可做变现门控。降低"每次手写 python"成本。

## 2. Stream 切分
- **OFFICE-1a 镜像 variant 机制**：manifest `image_variant` + supervisor `_select_image` + acquire 字段 + orchestrator 链路。先用现有 minimal 镜像验证机制（不依赖 office 镜像就绪）。
- **OFFICE-1b office 镜像**：`infra/sandbox-image-office/Dockerfile`（slim + 库 + 中文）+ CI 构建 + supervisor settings 接线。
- **OFFICE-3 办公 Skill 包**：平台 Skill（中文办公模板）。
- **OFFICE-2 国内连接器**：暂缓（backlog）。

依赖：`OFFICE-1a → OFFICE-1b`（机制先于镜像，但 1a 用 minimal 可独立验）；`OFFICE-1b → OFFICE-3`（Skill 用办公库需镜像就绪）。

## 3. CI / 约束
- **manifest schema 改动**（SandboxSpec 加 image_variant）走 protocol 包；无 DB migration（image_variant 是 runtime，不入库）。
- office 镜像 build：`python:3.12-slim` + pip install 后卸 pip；CI 加构建步骤（现状 CI 不构建 sandbox 镜像，需新增 office 镜像 build/tag）。
- 内存：纯 Python 库内存可控（不像 libreoffice）；大文件处理仍受 512MB 限，文档注明上限。
- gVisor：纯 Python 库（C 扩展 pandas/numpy/Pillow）在 gVisor 的兼容性需 integration 验（比 libreoffice 风险低）。
- 每 PR 零技术债 + 同步 ITERATION-PLAN（[[feedback_iteration_plan_sync_after_ship]]）；push 前 preflight。

## 4. Verification
- **OFFICE-1a**：manifest `image_variant: office` → acquire 收到 variant → supervisor `_select_image` 选对镜像名；None/未知 → 默认 minimal（向后兼容，现有 agent 不变）。
- **OFFICE-1b**：office 镜像内 `import pandas/openpyxl/docx/pptx/pypdf/pdfplumber/PIL` 成功；生成含中文的 xlsx/docx/pptx + matplotlib 图表不乱码；运行时无 pip。
- **OFFICE-3**：办公 Skill 可绑定、lazy_load 摘要进 prompt、skill_view 取全文；中文威胁扫描通过。

**完成 = 国内办公场景文件处理能力生产级 + 办公 Skill 可用**（连接器待生态）。
