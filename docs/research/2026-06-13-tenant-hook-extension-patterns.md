# 租户级 hook 扩展点 — 业界模式对比（HX-9 拍板输入稿）

> 2026-06-13。HX-9（Wave 3 架构级）的**评审前对比材料**——按「方向级决策先完整对比讨论再设计」流程,
> 本文不是设计文档,是拍板输入;拍板后才出 STREAM 级详设。
> ITERATION-PLAN 现行一句话定义:"manifest 声明式 hook(webhook 回调式起步,非任意代码)——设计 PR
> 与中心化治理路线统一评审后再实现"。

## 1. 问题定义 + 内部现状取证

**要解决什么**:租户想在 agent 生命周期的关键点挂自己的逻辑(run 完成通知内部系统 / 审批请求转发
到自家 IM / 产物生成后触发下游 ETL),今天只能轮询 API 或消费 SSE——没有平台主动推送的扩展点。

内部接缝(file:line):

| 事实 | 位置 | 含义 |
|---|---|---|
| manifest 已有 `hooks: dict[str, str]` 字段,**占位未接线**(零消费方) | `protocol/agent_spec.py:811` | schema 接缝已留;语义未定 |
| `run_event` 表 + RunEventStore + SSE 双路径(live attach + replay)已交付 | Stream H.3(#289–294) | 事件源现成——hook 触发源可直接复用,不需要新事件管道 |
| triggers(cron/webhook 入站起 run)已交付 | Stream J.10 | 入站已有;hook 是它的**出站对偶**(run 状态 → 租户 URL) |
| 凭证代理 egress 域名管控 + `validate_remote_url` 私网阻断已有 | credential-proxy / Stream W | SSRF 防御组件可复用([memory:url-template-host-pivot] 教训在册) |
| audit + token_usage 计量在位 | G.9 / Stream K | hook 投递可记账可审计 |

## 2. 业界四模式对比

### 模式 A — webhook 回调式（Stripe / GitHub / Svix 范式）

平台在事件发生时 POST 租户注册的 URL。业界成熟度最高的多租户扩展点形态。

- **签名**:HMAC-SHA256 是事实标准(Stripe `stripe-signature` / GitHub `X-Hub-Signature-256` /
  Slack `X-Slack-Signature`);**per-endpoint 独立 signing secret**(Firma 的 workspace 隔离范式)。
- **重试**:指数退避 + 抖动(防 thundering herd);Stripe 生产档重试 3 天 / GitHub ~24h;
  **4xx 不重试**(配置错误非瞬态);耗尽进 **DLQ 不静默丢**。
- **隔离**:**per-tenant 队列分区 + per-endpoint 断路器**——一个租户的慢端点绝不反压其他租户
  (多租户 SaaS 的核心纪律);超时窗口短(Stripe 30s / GitHub 10s)。
- **SSRF**:URL 注册时校验 + **解析后 IP 再校验**(防 DNS rebinding),私网段/云 metadata 端点阻断。
- 自建 vs 托管:Svix 等开源/托管投递平台存在,但引第三方与数据面隔离冲突——Expert Work 多租户数据
  不出平台,自建薄版(队列 + 退避 + DLQ)与现有 PG/worker 基建同构。

**适配度**:与 ITERATION-PLAN 既定方向一致;与 client-only 边界一致(出站 HTTP,不引入新协议面);
治理点清晰(注册审核 / 出口管控 / 计量)。代价:投递子系统(队列/退避/DLQ/断路器)是真工程量。

### 模式 B — 进程内 middleware / 生命周期 hook（LangChain v1 middleware 范式）

`before_agent / before_model / after_model / after_agent` 进程内插槽,跨切面(限流/审计/改写)。

**适配度**:这是**平台自己**的扩展机制(Expert Work 已有 compressor/estimator 等内部接缝),**不是租户级**
——租户代码进程内执行 = 任意代码,违背"非任意代码"的既定边界。**排除作为 HX-9 主体**;
M1-F 中间件路线继续走平台内部演进。

### 模式 C — 代码插槽（plugin / `code.package`）

租户上传代码在沙箱执行(M1-F2 Python 插槽路线,gVisor 7/7 用例是前置)。

**适配度**:能力上限最高,但安全/治理面最重(AST 审查 + 沙箱 + 插件生命周期管理);
ITERATION-PLAN 已显式排除("非任意代码")且 M1-F2 另有归属。**不在 HX-9 范围,不重复立项**。

### 模式 D — 事件流订阅（SSE / 队列拉取）

租户消费 `GET .../events` SSE 或未来的事件队列。

**适配度**:**已存在**(H.3 run_event SSE)。拉模式——租户要常驻连接/轮询,不解决"平台主动通知"
的需求;但它是 hook 的**事实来源**:hook 投递 = run_event 流的服务端消费者,语义天然一致
(同一事件序列,seq 去重,[memory:last-event-id] 语义可复用)。

## 3. 与中心化治理路线的协同（评审重点）

[memory:platform-centralized-governance] 的统一评审要求,hook 的治理答案:

- **注册治理**:hook URL 是租户配置资产——走 manifest 声明(版本化/修订史/HX-5 回滚全继承)
  还是平台 API 注册(运行时可改/不触发 agent 版本)?**这是拍板点 ①**。两者治理面不同:
  manifest 路线复用 agent_spec_revision 审计;API 路线像 triggers(独立 CRUD + audit)。
- **出口管控**:hook 投递是平台代租户发起的 egress——SSRF 校验 + 域名 allowlist(可选租户级
  收紧)与凭证代理的 egress 纪律同构;**hook 请求绝不携带平台凭证**(纯通知 + 签名)。
- **计量**:投递次数/失败率进 token_usage 同款计量面(chargeback 可定价);per-tenant 配额
  (业界参考:1M events/day 级别上限)。
- **爆炸半径**:per-tenant 队列 + 断路器,与 HX-10 的"爆炸半径由跨租户决定"同一公理。

## 4. 推荐(待拍板)

**起步 = 模式 A(webhook 回调)+ 模式 D 作触发源**,与 ITERATION-PLAN 既定方向一致:

1. 事件源:复用 run_event 序列(run 终态/审批请求/artifact 产出三类起步),不建新事件管道。
2. 投递:自建薄版——PG 队列表 + worker(指数退避+抖动 / 4xx 不重试 / DLQ)+ per-endpoint
   断路器 + per-tenant 并发上限。与现有 worker 基建(FeedbackConsumerWorker 同款形态)同构。
3. 安全:HMAC-SHA256 per-endpoint secret(secret_store 写穿,与 HX-8 tenant secret 同库)+
   注册时和解析后双段 SSRF 校验 + 不带凭证。
4. 事件 schema:`{event_id, event_type, occurred_at, tenant_id, payload}` + seq 去重指引
   (at-least-once 语义,消费方幂等)。

### 拍板点

| # | 问题 | 选项 | 倾向 |
|---|---|---|---|
| ① | hook 注册面 | manifest `hooks` 字段接线(版本化) vs 平台 API CRUD(运行时改,triggers 同款) | API CRUD——hook URL 是运维配置非 agent 行为,改 URL 不应弹 agent 版本;manifest `hooks` 字段届时标 deprecated 或转引用 |
| ② | 起步事件集 | run 终态 only vs +审批请求 +artifact 产出 | 三类起步(审批转发是租户最强需求场景) |
| ③ | 投递基建 | 自建薄版 vs 引 Svix(开源自托管) | 自建——与 PG/worker 基建同构,避免新组件运维面;Svix 复杂度为通用平台设计,Expert Work 事件集窄 |
| ④ | 与 HX-9 名义范围 | 仅出站 webhook vs 也含"hook 改写 run 行为"(如 pre-run 校验回调) | 仅出站通知起步——同步改写回调把租户端点拉进 run 关键路径(延迟+可用性耦合),违背 fail-open 公理;改写类需求归 M1-F 中间件评审 |

### 显式不做(本期)

- 进程内租户代码(模式 B/C)——边界已定"非任意代码";M1-F2 另有归属。
- 同步阻塞回调(拍板点 ④ 倾向)——租户端点进关键路径 = 可用性耦合。
- 引入消息中间件(Kafka 等)——事件量级(per-run 个位数事件)PG 队列表绰绰有余,M2 再议。

## Sources

- [Webhook security checklist (Aikido)](https://www.aikido.dev/blog/webhook-security-checklist) — SSRF/解析后 IP 校验/签名
- [Workspace Webhooks: multi-tenant isolated delivery (Firma)](https://firma.dev/insights/workspace-webhooks-multi-tenant) — per-tenant 队列分区/断路器/per-endpoint secret
- [Building a Reliable Service for Sending Webhooks (Hookdeck)](https://hookdeck.com/blog/building-reliable-outbound-webhooks) — 队列缓冲/退避/可观测
- [Stripe webhooks docs](https://docs.stripe.com/webhooks) + [Svix Stripe review](https://www.svix.com/resources/webhook-reviews/stripe-webhooks-review/) — 3 天退避重试/签名/30s 超时
- [Webhook Reliability 2026: Idempotency & Retry Reference](https://www.digitalapplied.com/blog/webhook-reliability-idempotency-retries-engineering-reference-2026) — 去重/退避/DLQ/签名四层
- [Webhook Delivery Guarantees — At-Least-Once, Retries, HMAC & Dead Letters](https://codelit.io/blog/api-webhooks-delivery-guarantee) — at-least-once 语义/DLQ 纪律
- [LangChain custom middleware docs](https://docs.langchain.com/oss/python/langchain/middleware/custom) — before/after_agent/model 进程内插槽(模式 B 参照)
- [Extensibility in AI Agent Frameworks (GoCodeo)](https://www.gocodeo.com/post/extensibility-in-ai-agent-frameworks-hooks-plugins-and-custom-logic) — hooks/plugins 分型
- [AI Agent Plugin and Extension Architecture (Zylos)](https://zylos.ai/research/2026-02-21-ai-agent-plugin-extension-architecture) — 插件生命周期 hook(模式 C 参照)
