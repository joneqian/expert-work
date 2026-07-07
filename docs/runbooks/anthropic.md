# Runbook — LLM Provider（Anthropic / 国内 provider）

> Stream G.3 故障预案。LLM provider 故障 = agent 无法推理。
> M0 已有 **fallback chain**（Stream E.11）+ 断路器（E.4）+ provider 层限流（E.12），
> 多数单 provider 故障应被自动降级吸收 —— 本 runbook 处理降级仍不够时的人工介入。

## 故障现象

- agent run 大量失败，错误指向 LLM 调用（`expert_work.llm_gateway.provider_request` span ERROR）。
- 日志可见断路器打开：`llm_error_handling` 中间件 `circuit open`。
- TTFT 飙升或 run 超时。

## 诊断

1. **范围**：单 provider 还是全部？查日志按 `provider` 区分
   （anthropic / kimi / glm / deepseek / qwen / doubao）。
2. **断路器状态**：日志查 `circuit` 状态 —— open 表示该 provider 已被自动摘除。
3. **provider 侧**：查对应 provider 的 status page / 控制台；确认是限流（429）、
   鉴权（401，key 失效）、还是 provider 宕机（5xx）。
4. **fallback 是否生效**：fallback chain 应在主 provider open 后切到下一个；
   若全链路 provider 都 open → 全部不可用。
5. **凭据**：LLM key 经 SecretStore 解析（ADR-0007 KMS）；
   key 失效查 `expert_work.credential` 相关日志。

## 处置

- **单 provider 限流 / 抖动**：通常无需人工 —— 断路器 + fallback 自动降级；
  观察 fallback provider 是否扛住流量。必要时临时调低该 provider 的限流配额
  让断路器更快摘除。
- **单 provider key 失效**：在 SecretStore（KMS）轮换 key；短 TTL 缓存
  （static 60s）过期后自动重拉，无需重启。
- **全 provider 不可达**：M0 无更多兜底 —— 这是已知风险（架构风险登记表）。
  缓解：(a) 确认不是本侧网络/出网问题；(b) 通知业务方降级；
  (c) provider 恢复后断路器半开探测自愈。
- **流量打爆自家 key**：E.12 provider 层限流应拦住；若仍超额，调 manifest /
  租户 quota（Stream C.5）压低并发。

## 回滚

provider 故障非本侧发布引入时无需回滚。
若由一次 manifest / provider 配置变更引入（如改错 model 名 / endpoint）→
回滚该 manifest 版本。

## 升级

P1（全 provider 失败）：自动降级无法吸收 → 升级；
同时确认是 provider 侧大规模故障还是本侧凭据 / 网络问题。
