# Sandbox 隔离纵深：业界实证调研（HX-10 决策依据）

> 2026-06-12。为 HX-10（sandbox 安全纵深：seccomp + gVisor + Trivy）做"是否现在上 gVisor / 各档门禁强度"决策前的实证调研。四角并行检索：业界沙箱隔离架构 / gVisor 生产代价 / 镜像 CVE 扫描门禁 / prompt-injection 下 agent 代码执行威胁模型。
>
> 证据分级：**[有数据]** = 可复现 benchmark 或具体数字；**[厂商实测]** = 官方生产规模实测；**[厂商声称]** = 单方博客说法；**[第三方]** = 安全研究者逆向/独立分析；**[传闻]** = 二手论坛口径。

## 0. 净结论（先行）

1. **业界共识：隔离强度匹配信任画像。** 可信内部代码 runc+seccomp 足够；agent 生成 / 多租户 untrusted 代码 → gVisor 或 microVM。我们 = 多租户 untrusted + DooD，按共识落 gVisor/Kata 档。
2. **"gVisor 2-3x 损失"被夸大。** CPU-bound Python <3%（Ant 70% 应用 <1%）；Tencent 百万级 agentic-RL 沙箱（同构负载）生产验证，兼容问题仅 1.7%。真实代价集中在网络吞吐（1.7x）/ 随机小 IO（4x），CPU-bound 几乎无感。
3. **systrap 平台不需要 KVM/嵌套虚拟化**，标准国内云 ECS 可跑；阿里云 OpenSandbox / 腾讯云 Cube Sandbox 都已把 gVisor 作生产 agent 沙箱方案。
4. **最重磅数字（SANDBOXESCAPEBENCH，ICML 2026，英国 AISI）**：前沿 LLM（Claude Opus 4.5 / GPT-5）对容器逃逸——misconfig 类（暴露 docker socket / 特权 / 可写宿主挂载）**100% 成功**；内核 CVE 类即使 hardened 配置 **~40% 成功**。
5. **威胁模型修正**：内部可信 vs 对外开放，逃逸能力相同，差的只是入口向量 + 攻击者迭代次数。多租户共享宿主下逃逸 = 跨租户数据泄露。**所需隔离强度由跨租户爆炸半径决定，不由用户信任决定**；信任只降概率不改影响。

## 1. 业界沙箱隔离架构

| 厂商/项目 | 隔离技术 | 信任分级证据 |
|---|---|---|
| OpenAI Code Interpreter | 容器基础 + **部分高风险任务额外 gVisor**（"for *some* higher-risk tasks"）[第三方逆向+厂商] | 按风险分档，非全量 gVisor |
| Anthropic | **本地可信→OS sandbox**（macOS Seatbelt / Linux bubblewrap）；**云端不可信→gVisor**；egress proxy + JWT host allowlist [厂商官方+第三方逆向，证据最强] | 最清晰的按信任分级活样本 |
| E2B | Firecracker microVM（独立 kernel，~150ms 快照冷启）[厂商] | untrusted 全量 microVM |
| Modal | gVisor（定位"跑 untrusted user/agent code"）[厂商] | — |
| Cloudflare | 双层：V8 isolate（低风险快路径）+ 容器 Sandbox（强边界）[厂商+媒体] | 隔离强度分级 |
| Fly.io | Firecracker microVM over KVM [厂商] | — |
| Daytona | **Sysbox 运行时**（user-ns root 映射 + 选择性 syscall 拦截，**保留 DinD/DooD 兼容**）[厂商] | **与我们 DooD 约束最贴** |
| Northflank | 全谱 K8s RuntimeClass 切换：有嵌套虚拟化→Kata；无→gVisor [厂商] | 按环境分级落地 |
| GKE Agent Sandbox（2026） | 默认 gVisor + pluggable Kata；300 sandboxes/秒 [厂商有数据] | 开源 K8s SIG 项目 |

**Anthropic 金句（有据）**：「The weakest layer is the one you built yourself」——标准原语（gVisor/seccomp/hypervisor）都没破，破的全是自研 egress proxy。→ 我们的 credential-proxy / egress 是审计重点，别当"已解决"。

## 2. gVisor 生产代价

| 维度 | 真实 overhead | 证据 |
|---|---|---|
| 纯 CPU-bound | ≈0%（Ant 70% <1%，25% <3%）| [厂商实测] |
| 单次 syscall | 2.8x（systrap/KVM 最快档）| [有数据] HotCloud'19 |
| Python import / 小文件密集 | 历史重灾区，VFS2/DirectFS 后改善 50-75% | [厂商实测] |
| 随机小 IO | ≈4x 慢 | [有数据] KubeBlocks |
| 顺序大 IO | 反而快 5.5x | [有数据] |
| 网络吞吐 | ≈1.7x 慢（netstack Go 栈 GC）；延迟反而更低 | [有数据] |

- **systrap**（2023 起默认）：无虚拟化要求，普通 Linux VM 可跑，嵌套场景比 KVM 更快。
- **国内云**：标准 ECS 无嵌套虚拟化 → 走 systrap（非 KVM）。阿里云 OpenSandbox（Kata+gVisor）、腾讯云生产百万级沙箱已验证。
- **必前置验证的兼容坑**：io_uring（ENOSYS，新 async 库回退 epoll 后行为可能异常——Claude Code issue #27230 Bun 实例）；`/proc/sys/net` 缺失；随机小 IO 4x；网络 1.7x。
- **"业界弃用 gVisor"无实证**；保留并扩大有大量厂商实测（Google/Ant/Tencent/Modal）。
- **最同构先例 = Tencent 百万级 agentic-RL 沙箱**（跑 AI 生成代码），兼容问题 1.7%，值得直接对标其踩坑清单。

## 3. 镜像 CVE 扫描门禁

- **Aqua（Trivy 作者）官方推荐生产管线卡 `CRITICAL,HIGH`**；不卡 LOW/MEDIUM 避免 alert fatigue。
- **`--ignore-unfixed` 普遍推荐**：门禁只在"有补丁可升级"时 fail——"修不了一个还没补丁的东西"，把门禁从噪音变可行动信号。
- **LibreOffice 类大依赖镜像卡 HIGH 会恒红 → 门禁失效**（开发者习惯性 ignore，CRITICAL 真信号被淹）。社区实证（shelfio/libreoffice-lambda issue #54）。
- **PR 阻断 vs 周期扫描是互补分工**：PR 防新增，weekly registry rescan 防存量漂移（新 CVE 每天披露，CI 只抓 build-time 已知）。
- **降噪手段**：带过期日期的 `.trivyignore`（强制复审禁永久 mute）、VEX（方向但生态不成熟，arxiv 2503.14388）、reachability。
- **沙箱镜像 CVE 优先级可下调**：隔离边界应是内核边界（gVisor/microVM）不是镜像内零 CVE；真高危是内核/逃逸类 CVE 非镜像内用户态库。**但不等于不扫**（供应链卫生/合规）。

**分镜像建议**：minimal(alpine)/debian-slim 卡 `CRITICAL,HIGH`+`--ignore-unfixed`；office(LibreOffice) 卡 `CRITICAL`+`--ignore-unfixed`，HIGH 走 weekly 报告。全部叠 `--ignore-unfixed`。

## 4. prompt-injection 下 agent 代码执行威胁模型

- **SANDBOXESCAPEBENCH（arXiv 2603.02277，ICML 2026 Oral）**：9 前沿 LLM vs 18 逃逸场景。misconfig（docker socket/特权/可写宿主挂载）**100% 逃逸**；内核 CVE 类 hardened 下 ~40%。**单条最 load-bearing 的数字**。
- **真实 RCE 案例**：CVE-2025-53773（GitHub Copilot/VS Code prompt injection → RCE）；GitHub MCP 跨仓泄露（lethal trifecta）。
- **runc 活体逃逸原语**：CVE-2025-31133 / -52565 / -52881（fd leak + mount ops），LLM 可驱动。
- **混淆代理（confused deputy）**：agent 持合法权限但无法内部区分可信指令 vs 不可信抓取内容 → 注入内容借用其权限。
- **各层挡什么**：

| 层 | 挡住 | 挡不住 |
|---|---|---|
| egress allowlist proxy | 外泄腿 / SSRF 到 metadata / C2 | 容器内代码执行 |
| credential proxy（secret 不进容器）| 凭证窃取（只能拿会话态随机串）| 逃逸 / 被代理能力本身的滥用 |
| seccomp | 收窄内核 CVE 逃逸所需 syscall 面 | allowed syscall 逃逸 / misconfig 逃逸 |
| cap-drop | 特权容器逃逸类（100% misconfig 之一）| 剩余 cap 内用户态利用 |
| **gVisor** | **内核 CVE 逃逸类**（guest syscall 打 Sentry 不直达宿主内核）| 注入本身 / 授权能力滥用 / ~70% syscall 兼容 |
| microVM | 最强（独立 guest kernel + KVM 硬件边界）| 注入本身 / 能力滥用 |

- **关键**：没有任何隔离层能阻止 prompt injection 本身——那是应用层/数据流问题。隔离只限制爆炸半径。根因解只有切 lethal trifecta（去数据访问 / 去不可信内容暴露 / 让外泄结构性不可能）。
- **OWASP LLM Top 10 2025**：LLM01 注入（根向量）/ LLM05 输出处理不当（→ CWE-78 命令注入、CWE-918 SSRF）/ LLM06 过度自主（agent 执行输出）/ LLM02 敏感信息泄露（外泄结果）。

**直答"runc+seccomp+cap-drop+egress+cred-proxy 够不够（不上 gVisor）"**：对"内部可信用户被注入 + 攻击者迭代受限"是可辩护边界——cap-drop 杀特权逃逸类、不挂 docker socket / 不可写宿主挂载杀另两个 100% misconfig 类、egress+cred-proxy 杀外泄腿。**残差 = 共享内核 CVE 逃逸类（hardened 下 LLM ~40% 可利用），影响是跨租户**。gVisor 正好且只关闭这一残差，是对外开放不可信用户前的标准门槛。

## 5. 对 HX-10 的落点

1. **最高优先级、零成本**：misconfig 断言——验证 docker socket 不进 sandbox、非特权、无可写宿主挂载（三个 100% 逃逸类），加测试钉死 + 设计显式断言。我们取证显示三项都不中（socket 挂 supervisor 不挂 sandbox / cap-drop ALL / read-only），但要钉死防回归。
2. **gVisor 当期纳入（可配置 + CI 验证）**：生产多租户同宿主推荐 runsc（代价实证可接受），dev runc；CI 验证按 Tencent 踩坑清单补 io_uring/proc-sys-net/随机IO 断言。
3. **Trivy 分镜像差异化 + `--ignore-unfixed`**（§3）。
4. **seccomp**：删 clone3（Docker default 本就允许，禁了崩新 glibc）；perf_event_open 移出强制（影响 profiling）；io_uring/userfaultfd/keyctl/bpf 保留禁用（安全收益实打实，io_uring 兼容代价文档标注）。
5. **Sysbox 作 gVisor 的 DooD 友好对照候选记一笔**（不必现在选）。

## 6. HX-10-F2 补充实证（2026-06-13）：gVisor fork-bomb 处置的业界做法

> gVisor CI 首跑暴露 gate_56：runsc 下 `--pids-limit` 限 sentry 宿主线程非 guest 进程，fork bomb → Go runtime 建线程失败 → sentry panic → 沙箱整体死（google/gvisor#2490）。隔离没破（爆炸半径=沙箱），但 runc 的「fork 报错、runner 存活」语义拿不到。决策前补一轮检索：**别人是不是也接受「沙箱阵亡+重建」**？结论——**是，方案 A 即业界标准**。

| 证据 | 内容 | 分级 |
|---|---|---|
| **gVisor 官方 Security Model** | 「gVisor relies on the host resource mechanisms (cgroups) for defense against resource exhaustion and denial of service attacks」——资源耗尽防御**设计上委托宿主 cgroup**，sentry 自身不兜。Resource Model 进一步：stub 进程受**沙箱级** PID 限约束。 | [厂商官方] |
| **google/gvisor #2490 / #2489 / #3942** | #2490 fork bomb → panic（"failed to create new OS thread"，非优雅 EAGAIN）；#2489 `pids.limit` 对 runsc 不生效；#3942 runsc 开海量 Go worker 线程撞任意 pids cgroup 限。**三 issue 2020 开至今 open**——gVisor 不当 bug 修，因立场=资源耗尽宿主 cgroup 兜、沙箱死重建。 | [厂商 issue tracker] |
| **Modal**（gVisor 跑 untrusted code） | 明确 ephemeral container lifecycle——"spin up and tear down as needed"，配 warm pool。即「死了重建」模型。 | [厂商] |
| **GKE Agent Sandbox（gVisor）** | Agent Sandbox CRD 跑 gVisor 隔离的 LLM 代码，用 **ephemeral environments + warm pools + Pod Snapshots**——重建是一等机制。 | [厂商] |
| **通用容器安全共识** | 「container isolation doesn't protect against process table exhaustion on the host, so limits must always be set」——防御点在**宿主层 cgroup 限总量**（防溢出邻居），**不在 guest 内优雅报错**。 | [第三方/Datadog] |

**落点（方案 A）**：宿主 cgroup 限总量（爆炸半径锁死沙箱）+ ephemeral 容器 + warm pool（HX-6 已在）+ reaper 重建（已在）= A 标准件齐全，**零生产代码改动**。workspace volume 持久（J.15）→ fork bomb 只丢 in-flight exec，用户数据零损失。否决 B（guest 内 cgroupfs `pids.max`）：逆 gVisor 设计、gVisor cgroupfs 是模拟实现不保证 `pids.max` 写穿、真 runsc 实测周期长且可能直接判不可行。gate_56 由 `xfail` 转正为显式 A 语义断言（沙箱阵亡 + supervisor 存活 + 重建成功）。

## Sources

**沙箱架构**：[OpenAI 逆向](https://ryan.govost.es/2025/openai-code-interpreter/) · [Anthropic containment](https://www.anthropic.com/engineering/how-we-contain-claude) · [Anthropic Claude Code sandboxing](https://www.anthropic.com/engineering/claude-code-sandboxing) · [E2B/Manus](https://e2b.dev/blog/how-manus-uses-e2b-to-provide-agents-with-virtual-computers) · [Modal](https://modal.com/resources/best-sandbox-infrastructure-multi-tenant-ai-apps) · [Cloudflare Dynamic Workers](https://blog.cloudflare.com/dynamic-workers/) · [Fly.io Firecracker](https://fly.io/learn/firecracker-vm/) · [Daytona/Sysbox](https://www.daytona.io/docs/en/security-exhibit/) · [Northflank Kata vs gVisor](https://northflank.com/blog/kata-containers-vs-gvisor) · [GKE Agent Sandbox](https://cloud.google.com/blog/products/containers-kubernetes/bringing-you-agent-sandbox-on-gke-and-agent-substrate) · [信任分级 framing](https://www.shayon.dev/post/2026/52/lets-discuss-sandbox-isolation/) · [Docker untrusted workloads](https://www.docker.com/blog/untrusted-autonomous-workload-ai-sandboxes/)

**gVisor 代价**：[HotCloud'19 True Cost](https://www.usenix.org/system/files/hotcloud19-paper-young.pdf) · [官方 Performance Guide](https://gvisor.dev/docs/architecture_guide/performance/) · [Systrap release](https://gvisor.dev/blog/2023/04/28/systrap-release/) · [Platform Guide](https://gvisor.dev/docs/architecture_guide/platforms/) · [Ant 生产规模](https://gvisor.dev/blog/2021/12/02/running-gvisor-in-production-at-scale-in-ant/) · [Tencent 百万级 agentic-RL](https://gvisor.dev/blog/2026/04/23/scaling-agentic-rl-sandboxes-to-the-millions-with-gvisor-at-tencent/) · [DirectFS](https://opensource.googleblog.com/2023/06/optimizing-gvisor-filesystems-with-directfs.html) · [syscall 兼容表](https://gvisor.dev/docs/user_guide/compatibility/linux/amd64/) · [Claude Code io_uring #27230](https://github.com/anthropics/claude-code/issues/27230) · [KubeBlocks benchmark](https://kubeblocks.io/blog/does-containerization-affect-the-performance-of-databases) · [阿里云 OpenSandbox](https://northflank.com/blog/alibaba-opensandbox-architecture-use-cases)

**CVE 扫描**：[Trivy CI/CD](https://trivy.dev/docs/latest/ecosystem/cicd/) · [trivy-action](https://github.com/aquasecurity/trivy-action) · [ignore-unfixed](https://trivy.dev/docs/latest/scanner/vulnerability/) · [Filtering/.trivyignore](https://trivy.dev/docs/latest/configuration/filtering/) · [VEX](https://trivy.dev/docs/latest/supply-chain/vex/repo/) · [oneuptime severity](https://oneuptime.com/blog/post/2026-01-28-trivy-severity-filtering/view) · [Wiz 误报来源](https://www.wiz.io/academy/container-security/container-security-scanning) · [VEX 成熟度 arxiv 2503.14388](https://arxiv.org/pdf/2503.14388) · [libreoffice 镜像 issue #54](https://github.com/shelfio/libreoffice-lambda-base-image/issues/54)

**HX-10-F2 fork-bomb 处置（2026-06-13 补）**：[gVisor #2490 fork bomb panic](https://github.com/google/gvisor/issues/2490) · [gVisor #2489 pids.limit not working](https://github.com/google/gvisor/issues/2489) · [gVisor #3942 runsc thread explosion](https://github.com/google/gvisor/issues/3942) · [gVisor Security Model](https://gvisor.dev/docs/architecture_guide/security/) · [gVisor Resource Model](https://gvisor.dev/docs/architecture_guide/resources/) · [Modal untrusted code](https://modal.com/resources/run-untrusted-code-safely) · [GKE Sandbox gVisor](https://oneuptime.com/blog/post/2026-02-09-gke-sandbox-gvisor-workload-isolation/view) · [Datadog cgroups fundamentals](https://securitylabs.datadoghq.com/articles/container-security-fundamentals-part-4/)

**威胁模型**：[SANDBOXESCAPEBENCH arXiv 2603.02277](https://arxiv.org/abs/2603.02277) · [bench repo](https://github.com/UKGovernmentBEIS/sandbox_escape_bench) · [lethal trifecta](https://simonw.substack.com/p/the-lethal-trifecta-for-ai-agents) · [confused deputy NCSC](https://www.ncsc.gov.uk/blog-post/prompt-injection-is-not-sql-injection) · [indirect injection Lakera](https://www.lakera.ai/blog/indirect-prompt-injection) · [runc CVEs Sysdig](https://www.sysdig.com/blog/runc-container-escape-vulnerabilities) · [gVisor security](https://gvisor.dev/security/) · [gVisor vs CVE-2020-14386](https://cloud.google.com/blog/products/containers-kubernetes/how-gvisor-protects-google-cloud-services-from-cve-2020-14386) · [credential proxy 应用层防御](https://dev.to/uenyioha/application-layer-defense-stopping-exfiltration-inside-the-sandbox-4l6c) · [AgentCore 隔离绕过 Unit42](https://unit42.paloaltonetworks.com/bypass-of-aws-sandbox-network-isolation-mode/) · [OWASP LLM Top 10 2025](https://owasp.org/www-project-top-10-for-large-language-model-applications/assets/PDF/OWASP-Top-10-for-LLMs-v2025.pdf)
