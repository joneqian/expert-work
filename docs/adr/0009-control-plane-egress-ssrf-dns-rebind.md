# ADR-0009：control-plane 出站 SSRF / DNS-rebind 防御 — 基础设施 egress 策略 + 应用层静态检查

- **状态**：✅ 已决策（Stream MCP-OAUTH；MCP 基建加固 follow-up #3）
- **日期**：2026-06-06
- **决策依据**：MCP 基建加固审计 follow-up #3；[`url_validation.py`](../../packages/expert-work-common/src/expert_work/common/url_validation.py) 现有静态 SSRF guard；[ADR-0008 数据 at-rest 加密](./0008-data-at-rest-encryption.md)（同样"M0 用基础设施层能力，不在应用栈引入额外依赖"的取向）；F.9 沙箱 egress 隔离（`expert-work-sandbox-egress` `--internal` 网络）
- **背景**：control-plane 会 connect-out 到**租户/平台提供的 URL**（远程 MCP server、OAuth 授权服务器）。这是一个 SSRF 暴露面。应用层已有静态 URL 校验，但它**挡不住 DNS-rebind**。本 ADR 记录"DNS-rebind 防御放在哪一层"的决议，以免后人误以为还需在应用层补一个 resolve-then-pin 半成品。

---

## TL;DR

- **DNS-rebind 防御不在应用层做**：应用层做 resolve-then-pin（解析后钉住 IP 再连）在异步 HTTP 栈里脆弱、易错、维护成本高，且仍有 TOCTOU 残窗。
- **依赖基础设施 egress 策略**：部署须限制 control-plane 的出方向网络，禁止其连 RFC1918 私网 / loopback / 链路本地（含云元数据 `169.254.169.254`）。网络层钉死"能连哪"，**天然免疫 DNS-rebind**（域名无论解析到什么，私网目的地直接被网络拒绝）。
- **保留应用层静态检查**：`validate_remote_url` 作纵深防御第一层（快速拒明显坏 URL、省一次出站、给出清晰报错）。
- **沙箱 egress 隔离（F.9）不覆盖本路径**：`expert-work-sandbox-egress` 只隔离**沙箱内**出站；control-plane 进程自身的 connect-out 是另一条路径，需要独立的 egress 控制。

---

## 1. 上下文

### 1.1 暴露面：control-plane 的 connect-out

租户按 URL 注册远程 MCP server，平台按 URL 声明 OAuth 连接器；control-plane 随后**主动连出**到这些 URL。所有 connect-out 站点都在 control-plane 进程内（**不经沙箱**）：

| 站点 | 文件 | 触发 |
|---|---|---|
| 注册校验 | `api/mcp_servers.py` | 租户注册/更新 MCP server |
| 探活 | `mcp_probe.py` | 注册时/手动探测连通性 |
| OAuth 发现 + token | `mcp_oauth.py` | OAuth initiate / callback / 刷新 |
| 运行时工具调用 | `runtime.py` | agent 调用 MCP 工具 |

URL 的 host 部分**来自外部输入**（租户填、或平台 catalog 填）。未加约束的 connect-out 可被诱导去连内部服务或云元数据端点 —— 经典 SSRF。

### 1.2 威胁模型

- **静态 SSRF**：URL 直接写私网/loopback/元数据 IP，或 localhost 名、非规范 IP 字面量（十进制 `2130706433`、十六进制 `0x7f000001`、短点分 `127.1`）。
- **DNS-rebind（TOCTOU）**：URL 用攻击者控制的域名。校验时该域名解析为公网 IP（通过检查）；连接时（TTL=0 重绑）解析为私网/元数据 IP。**校验点与使用点之间的时间差**让静态检查失效。

### 1.3 现有缓解

**应用层 — `validate_remote_url`**（`expert-work-common`，每个 connect-out 站点调用）：
- 拒非 `http(s)` scheme、无 host、localhost 名；
- 拒 IP 字面量为 private / loopback / link-local（含 `169.254.0.0/16` 元数据）/ reserved / multicast / unspecified；
- 拒非规范 IP 字面量（十进制/十六进制/短点分）。
- **不解析 DNS** —— 故**挡不住 DNS-rebind**，也挡不住"域名解析到私网"。这一限制本就写在该模块 docstring 里。

**基础设施 — 沙箱 egress 隔离（F.9）**：`expert-work-sandbox-egress` 为 `--internal` Docker 网络（无 NAT/默认路由），沙箱只能连同网 peer（credential-proxy），实测拒 `169.254.169.254`。**但它只保护沙箱内出站**；control-plane 自身的 connect-out 不在该网络里。

---

## 2. 决策

### 2.1 DNS-rebind 防御放在基础设施 egress 层，不在应用层

**不实现应用层 DNS-rebind 防御**（resolve-then-pin / 自定义 resolver / pin 已解析 IP 再连）。理由：

1. **脆弱**：要在异步 `httpx` 栈里可靠地"解析→校验每个候选 IP→强制连接用同一 IP（含重定向、连接复用、HTTP/2、IPv4/IPv6 双栈）"，需自定义 transport/resolver，复杂且易出现绕过。
2. **仍有残窗**：pin 之后到实际 syscall 之间理论上仍有 TOCTOU；要彻底关闭得下沉到 socket 层。
3. **维护成本**：自定义网络栈是长期负担，与"M0 优先用基础设施能力、不在应用栈加依赖"的取向（[ADR-0008](./0008-data-at-rest-encryption.md)）相悖。
4. **网络层更彻底**：egress 策略不关心域名解析成什么 —— 私网目的地在网络层被直接拒绝，对 DNS-rebind **天然免疫**，且对所有 connect-out 站点统一生效（无需逐站点改代码）。

### 2.2 保留应用层静态检查作纵深防御

`validate_remote_url` 继续在每个 connect-out 站点调用，作为第一层：快速拒明显恶意/笔误 URL、避免无谓出站、给租户清晰的报错。它是 egress 策略的补充，不是替代。

### 2.3 部署要求（egress 策略）

部署 Expert Work 时**必须**对 control-plane（及任何代其 connect-out 的组件）施加出方向网络限制，至少禁止连：

- RFC1918 私网：`10/8`、`172.16/12`、`192.168/16`；
- loopback `127/8`、`::1`；
- 链路本地 `169.254/16`（含云元数据 `169.254.169.254`）、`fe80::/10`；
- 其余 reserved / unspecified 段。

实现手段（按部署环境择一，能力等价）：

- **阿里云**（本项目生产环境）：VPC 安全组/网络 ACL 出方向规则，或 NAT 网关 + 出方向白名单；
- **Kubernetes**：`NetworkPolicy` egress 规则（`ipBlock` 排除上述私网段）或 Cilium/Calico 等价策略；
- **出站代理**：强制 control-plane 出站经 egress proxy，proxy 侧按 allowlist 放行已知 MCP/OAuth 域。

> 类比：沙箱已用 `expert-work-sandbox-egress`（`--internal`）把出站锁死到 credential-proxy（F.9）。control-plane 因需连真实公网 MCP/OAuth 服务，不能用 `--internal`，但应用**等价的出方向私网拒绝策略**达到同一目的。

---

## 3. 备选方案（已否决）

| 方案 | 否决理由 |
|---|---|
| 应用层 resolve-then-pin | 见 §2.1：脆弱、复杂、残留 TOCTOU、长期维护负担。 |
| 自定义 `httpx` resolver/transport 校验每个候选 IP | 同上；且需覆盖重定向/连接复用/双栈，易绕过。 |
| 仅靠现有静态检查 | 不足 —— 明确挡不住 DNS-rebind 与"域名→私网"。 |
| 什么都不做 | 留 SSRF/DNS-rebind 暴露面，不可接受。 |

---

## 4. 残留风险与重评条件

- **残留风险**：若某部署**未**正确施加 egress 策略，则 DNS-rebind/SSRF 暴露面回归（仅剩静态检查）。→ 缓解：本 ADR + 部署 runbook 列为**硬性部署前提**；上线检查单核对。
- **重评条件**：若未来需要在**无法施加网络层 egress 控制**的环境运行 control-plane（如某些 PaaS），则重新评估应用层 socket 级 pin 方案，并更新本 ADR。

---

## 5. 影响

- **代码**：无新增/改动逻辑。`url_validation.py` docstring 更新为指向本 ADR（说明 DNS-rebind 由基础设施 egress 层负责，而非"待补的应用层 follow-up"）。
- **部署**：egress 策略成为 control-plane 的**部署前提**（§2.3）。
- **后续**：可在部署 runbook / 上线检查单补一条 egress 策略核对项（非本 ADR 范围）。
