# 沙盒隔离技术对比调研

> 调研日期：2026-05
> 调研范围：12+ 沙盒方案 + 主流 AI 公司实践
> 结论：M0/M1 用 **Docker + gVisor (runsc)**，M3 升级 **Kata Containers**

---

## 关键技术对比

### 性能基准（单实例）

| 方案 | 隔离强度 | 冷启动 | 温启动 | 内存/实例 | 单机密度 | 备注 |
|------|----------|--------|--------|-----------|----------|------|
| Docker | 弱（共享内核）| 1-3s | 100-500ms | 50-200MB | 100-500 | 不可信代码不够 |
| **gVisor (runsc)** | 中（用户态内核拦截）| 200-500ms | 200-500ms | 150-500MB | 50-150 | OpenAI/Claude 同款 |
| **Firecracker** | 强（KVM microVM）| 125ms | 50-100ms | <5MB | 数千 | AWS Lambda 同款 |
| **Kata Containers** | 强（K8s + Firecracker/QEMU）| 0.5-2s | 200-500ms | 5-50MB | 500-2k | K8s 多租户首选 |
| WASM (wasmtime) | 中（SFI + 能力）| 1-100μs | <1ms | <1MB | 数万 | 仅轻量计算 |
| Cloudflare V8 Isolate | 进程级 | <10ms | <1ms | 几 MB | 1M req/s | 仅 JS |

### 安全性对比

| 技术 | 逃逸风险 | 内核漏洞影响 | 推荐度 |
|------|---------|-------------|--------|
| Docker | 极高 | 直接威胁主机 | ❌ untrusted code |
| **gVisor** | 中 | 需同时利用 2 个内核 | ✅ 短期执行 |
| **Firecracker** | 低 | KVM 漏洞不直接波及 | ✅✅ 首选 |
| **Kata** | 低 | KVM 漏洞不直接波及 | ✅✅ K8s 首选 |
| WASM | 中-高 | JIT 编译器漏洞为主 | ⚠️ 简单计算 |
| K8s Pod（默认 runc）| 极高 | 共享内核 | ❌ untrusted |

**2024-2025 CVE 趋势**：
- runc 类漏洞持续出现（年 3-4 个重要 CVE）
  - CVE-2024-21626（Leaky Vessels）：runc 文件描述符泄漏
  - CVE-2025-31133 / 52565 / 52881：mount race condition 导致主机路径写入
- 核心问题：**容器与主机内核共享**
- 结论：不可信代码必须用 microVM 级隔离（Firecracker/Kata）

---

## 主流 AI 公司沙盒实践

| 公司/产品 | 技术 | 关键特点 |
|-----------|------|---------|
| **OpenAI Code Interpreter** | gVisor | Jupyter kernel 子进程 + websocket RPC，1GB 内存默认 |
| **Claude Code（本地）** | bubblewrap (Linux) + seatbelt (macOS) | seccomp BPF 阻止 AF_UNIX socket、Unix socket 凭证代理 |
| **Claude Managed Agents（云）** | gVisor + 6 namespaces + 自定义 PID 1 | 凭证代理在 sandbox 外部，credentials 永不进容器 |
| **AWS Lambda / Bedrock AgentCore** | Firecracker microVM | 8 年生产，数十亿次调用 |
| **Perplexity Sandbox API** | Kubernetes Pod | NetworkPolicy + 出口代理 |
| **Cloudflare Agents** | Durable Object（V8 Isolate）+ Sandbox SDK（容器）| 边缘部署，毫秒启动 |
| **Modal Labs** | gVisor + GPU | 唯一支持隔离 GPU 的沙盒 |
| **E2B** | Firecracker microVM | 专为 AI Agent，~50% Fortune 500 在用 |
| **Daytona** | microVM + 持久化文件系统 | 暂停/恢复 |

---

## 详细分析

### 1. Docker（基线 — 不推荐用于隔离）

**隔离机制**：Linux Namespace + cgroups（共享内核）

**为什么不够**：
- 2024 CVE-2024-21626（Leaky Vessels）
- 2025 三大关键 CVE 允许主机路径写入
- 容器逃逸：共享内核意味着内核 bug 直接威胁主机

**结论**：仅用于工具调用的轻量场景，**不可信代码必须升级**。

---

### 2. gVisor（推荐起步）

**项目地址**：https://github.com/google/gvisor

**隔离机制**：
- Sentry 进程拦截并处理应用 syscall（>70% 的 319 个 Linux syscall 已实现）
- 攻击者需同时利用 gVisor 和宿主机内核漏洞，攻击面更大

**性能开销**：
- syscall-heavy 工作负载：1.5-3x 慢
- IO-bound（LLM 任务）：<10%

**集成**：
- runsc OCI 兼容，与 Docker/Kubernetes 无缝集成
- K8s 中用 RuntimeClass 配置

**安全历史**：
- Google 内部 + GCP 基础设施已运行数年
- 无公开逃逸 CVE
- 设计时考虑对抗性工作负载

**对我们的契合度**：⭐⭐⭐⭐⭐
- 半可信场景刚好（OpenAI/Claude 实战方案）
- Docker 兼容（M0 docker-compose 直接用）
- 单机能跑 50-150 个 sandbox 满足初期

---

### 3. Firecracker（M3 升级路径）

**项目地址**：https://github.com/firecracker-microvm/firecracker

**隔离机制**：
- 每个 microVM 独立内核（KVM 硬件虚拟化）
- 最强隔离，硬件强制边界

**性能**：
- 冷启动 125ms（行业最快）
- 创建速率 150 microVM/秒
- 内存 <5MB/microVM（含内核）
- 单机能跑数千个

**适合场景**：
- 执行不可信代码（最高优先级）
- 长期运行 Agent + 持久化存储 + 快照
- 需要极高密度的多租户

**结论**：M3 阶段如果隔离要求升级（变成完全不可信代码），切换到 Firecracker。

---

### 4. Kata Containers（M3 K8s 推荐）

**项目地址**：https://katacontainers.io

**隔离机制**：
- 每个 Pod 运行独立轻量级 VM
- 与 K8s 原生集成（RuntimeClass）
- 底层可用 Firecracker（更轻）或 QEMU（更兼容）

**性能**：
- 冷启动 1-2s（取决于 VMM）
- 用 Firecracker 时接近 Firecracker 性能
- 单机 500-2000 Pod

**适合场景**：
- K8s 多租户环境
- 需要 VM 级隔离但保持 K8s 开发体验

**对我们的契合度**：⭐⭐⭐⭐⭐ 作为 M3 K8s 阶段升级路径

---

### 5. WebAssembly（不适用）

**优势**：
- 启动微秒级
- 内存 <1MB
- 跨平台

**劣势**：
- 生态不如 Linux（库支持有限）
- JIT 编译器漏洞引发逃逸（"The Wasm Breach" 2024）
- 仅适合轻量计算

---

## 外部托管沙盒服务

| 服务 | 技术 | 价格 | 何时考虑 |
|------|------|------|---------|
| **E2B** | Firecracker | ~$0.05/vCPU·h | 不想自建运维 |
| **Daytona** | microVM | ~$0.067/h + 存储 | 长任务持久化 |
| **Modal Labs** | gVisor + GPU | 按秒计费 | GPU Agent 场景 |
| **CodeSandbox SDK** | Firecracker | 不公开 | 短期开发环境 |
| **Cloudflare Sandbox SDK** | V8 + 容器 | Workers 定价 | 边缘部署 / JS Agent |

**结论**：我们的方案是**自建 + 可选回退到 E2B**（架构上 Sandbox Pool 接口已抽象，可同时支持自建和托管）。

---

## 推荐方案（按规模）

### A. 小团队快速起步（<5 人，<100 Agent）

**推荐**：E2B + Docker（开发环境）
- 成本可预测（$0/月起）
- 无需自建基础设施
- 限制：定制困难、依赖网络

### B. 中型团队（5-20 人，几百 Agent）

**推荐**：自建 Kata + 备用 E2B
- 完全可控
- 月成本 $400-500（K8s 集群 + 计算节点）
- 需要 DevOps 能力

### C. 大型企业（100+ 人，几千租户）

**推荐**：多区域 Kata + BYOC E2B 备用通道
- 多 AZ K8s + 自动故障转移
- 月成本 $55-60k
- 完整安全加固（PSS、OPA/Gatekeeper、定期渗透）

**我们当前选择**（中型团队，初期 <100 Agent）：
- M0/M1：Docker + gVisor 起步（不上 K8s，简化运维）
- M3：升级 K8s + Kata Containers

---

## 沙盒安全验证（必跑测试）

1. **文件隔离**：tenant A 写文件 → tenant B 启动 sandbox 后看不到
2. **进程隔离**：sandbox A 启 daemon → sandbox B `ps aux` 不可见
3. **网络隔离**：sandbox 内 connect `host.docker.internal`、`169.254.169.254`、Vault 内网 IP — 全部 refused
4. **凭证防泄漏**：sandbox 内 `env`、`/proc/self/environ`、`/var/run/secrets` 无真实凭证
5. **资源耗尽**：fork bomb 应被 PID limit 终止，不影响其他 sandbox
6. **Side channel**：用 `perf` 测量 timing，验证 gVisor syscall 拦截使主机 binary 不可被 fingerprint
7. **逃逸**：跑 CVE-2019-5736 (runc)、CVE-2022-0185 等已知 PoC，必须失败

---

## 关键参考资源

- [gVisor 官方文档](https://gvisor.dev/)
- [Firecracker GitHub](https://github.com/firecracker-microvm/firecracker)
- [Kata Containers](https://katacontainers.io/)
- [Kubernetes Agent Sandbox](https://agent-sandbox.sigs.k8s.io/) — 我们 M3 的蓝本
- [Claude Code 沙盒博客](https://www.anthropic.com/engineering/claude-code-sandboxing)
- [anthropic-experimental/sandbox-runtime](https://github.com/anthropic-experimental/sandbox-runtime) — 本地 sandbox 参考实现
- [Agent Sandbox Taxonomy](https://github.com/kajogo777/the-agent-sandbox-taxonomy) — 评估框架
