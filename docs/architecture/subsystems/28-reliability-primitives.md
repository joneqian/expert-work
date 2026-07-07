# 28 Reliability Primitives — 全链路 TLS / Health Probes / Graceful Shutdown / Timeout Hierarchy

> 4 个产品级横切关注点的统一规范。任何 Expert Work 服务（Control Plane / Orchestrator / Sandbox Supervisor / Credential Proxy / MCP Gateway / Admin UI）**首次创建**时即按本文档落地这 4 项基础设施，**不留后补**。

---

## 1. 职责 & 边界

### ✅ 做

- **全链路 TLS**：服务对外暴露 / 服务间互连 / 服务对外部依赖（Postgres / OSS / Vault / Keycloak）一律 TLS；M0 静态证书，M1 cert-manager 自动轮换
- **Health Probes**：每个服务暴露 `/healthz/live`、`/healthz/ready`、`/healthz/startup` 三种 probe；含依赖健康聚合
- **Graceful Shutdown**：SIGTERM 进入"drain 模式"，停止接新请求 → 完成在途 → 写 checkpoint → 关闭依赖连接 → 超时强 kill
- **Timeout Hierarchy**：5 层 timeout 嵌套（request > session > step > tool > LLM call），上层 deadline 自动剪裁下层；cancellation 信号端到端传播

### ❌ 不做

- 不做 mTLS 证书签发本身 → [15 AuthN/AuthZ](./15-authn-authz.md) §"mTLS 服务间认证"
- 不做 Sandbox 内进程的 timeout → [14 Sandbox Pool](./14-sandbox-pool.md)
- 不做应用层断路器（LLM provider 失败重试）→ [10 LLM Gateway](./10-llm-gateway.md)
- 不做集群级 DR / failover → [22 Disaster Recovery](./22-disaster-recovery.md)
- 不做限流 / quota → [16 Quota / Rate Limit](./16-quota-rate-limit.md)

---

## 2. 上下游依赖

| 依赖方向 | 子系统 | 关系 |
|---------|--------|------|
| 上游消费者 | 所有服务 | 必须实现 4 项规范才算 Stream 完成 |
| 下游证书 | [15 AuthN/AuthZ](./15-authn-authz.md) | 提供 mTLS 客户端/服务端证书 |
| 下游 OS | systemd / docker / k8s | 接收 SIGTERM 并等待 graceful 退出 |
| 横切 | [20 Observability](./20-observability.md) | 4 项行为均 emit metric + log + span（见 §7） |
| 横切 | [16 Quota](./16-quota-rate-limit.md) | timeout 决策考虑 quota 剩余预算 |
| 横切 | [99 Shared Types](./99-SHARED-TYPES.md) | `DeadlineContext` / `ShutdownSignal` / `HealthStatus` 类型定义 |

---

## 3. 数据模型 / 状态机

### 3.1 HealthStatus enum

```python
# packages/expert-work-common/src/expert_work/common/health.py
from enum import StrEnum
from dataclasses import dataclass

class HealthStatus(StrEnum):
    OK = "ok"                 # 完全正常
    DEGRADED = "degraded"     # 部分依赖不可用但仍能提供服务
    NOT_READY = "not_ready"   # 启动中 / 主动 drain / 关键依赖丢失
    UNHEALTHY = "unhealthy"   # 自身故障，需重启

@dataclass(frozen=True)
class HealthReport:
    status: HealthStatus
    service: str
    version: str
    checks: dict[str, HealthStatus]  # 依赖名 -> 状态
    started_at: float                # unix ts
    drain_started_at: float | None   # 进入 drain 后填
```

### 3.2 ShutdownState 状态机

```
   STARTING ──signal: ready──▶ RUNNING ──SIGTERM──▶ DRAINING ──in-flight=0──▶ STOPPING ──force_timeout──▶ KILLED
                                  ▲                    │
                                  │                    ▼
                                  └──── liveness fail ─┘ (重启)
```

| 状态 | 含义 | `/healthz/*` 响应 |
|------|------|------|
| `STARTING` | 启动中：DB 连接、初始化 fixtures | live=200, ready=503, startup=503 |
| `RUNNING` | 正常 | live=200, ready=200, startup=200 |
| `DRAINING` | 收到 SIGTERM，停接新请求 | live=200, ready=503, startup=200 |
| `STOPPING` | 在途完成，关闭连接 | live=200, ready=503, startup=200 |
| `KILLED` | 超时强杀（外部 kill -9） | — |

### 3.3 DeadlineContext（贯穿 timeout 层级）

```python
@dataclass(frozen=True)
class DeadlineContext:
    deadline_ms: float       # 绝对时间戳（unix ms）
    parent: DeadlineContext | None
    layer: str               # 'request' | 'session' | 'step' | 'tool' | 'llm'
    cancel_token: CancelToken  # 用户主动取消信号

    def remaining_ms(self) -> float:
        return max(0.0, self.deadline_ms - time.time() * 1000)

    def derived(self, layer: str, max_ms: float) -> "DeadlineContext":
        """派生子层 deadline；自动剪裁不超过父 deadline。"""
        budget = min(max_ms, self.remaining_ms())
        return DeadlineContext(
            deadline_ms=time.time() * 1000 + budget,
            parent=self,
            layer=layer,
            cancel_token=self.cancel_token,
        )
```

---

## 4. 关键接口

### 4.1 Health endpoints（每服务统一）

```python
# packages/expert-work-common/src/expert_work/common/health_endpoints.py
from fastapi import APIRouter, status

def make_health_router(service: HealthReportProvider) -> APIRouter:
    router = APIRouter(prefix="/healthz", tags=["health"])

    @router.get("/live")
    async def live() -> dict:
        """liveness：只检查自身进程死活。返回 200 表示进程活着，不代表能服务。"""
        report = await service.live()
        return {"status": report.status.value}

    @router.get("/ready")
    async def ready(response: Response) -> dict:
        """readiness：能否接收新请求。drain 时返回 503，让 LB 摘节点。"""
        report = await service.ready()
        if report.status not in (HealthStatus.OK, HealthStatus.DEGRADED):
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": report.status.value, "checks": {k: v.value for k, v in report.checks.items()}}

    @router.get("/startup")
    async def startup(response: Response) -> dict:
        """startup：启动是否完成。k8s 用这个延迟 liveness probe，避免冷启动期被误杀。"""
        report = await service.startup()
        if report.status != HealthStatus.OK:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": report.status.value}

    return router
```

`HealthReportProvider` 是每个服务自己实现的 Protocol：

```python
class HealthReportProvider(Protocol):
    async def live(self) -> HealthReport: ...
    async def ready(self) -> HealthReport: ...
    async def startup(self) -> HealthReport: ...
```

实现要点：
- `live` 必须**无依赖**（不能查 DB），否则数据库一抖就被 k8s 重启
- `ready` 必须**聚合依赖**：DB ping / Redis ping / Vault ping / OSS HEAD（依赖列表由各 service 定义）
- `startup` 检查启动完成标志（migrations 已跑、warmup 已完成等）

### 4.2 Graceful Shutdown Hook

```python
# packages/expert-work-common/src/expert_work/common/lifecycle.py
@dataclass
class Lifecycle:
    """每个服务的生命周期管理器；FastAPI startup/shutdown event 自动接入。"""

    drain_timeout_s: float = 30.0  # SIGTERM 后等待在途完成的最长时间
    force_kill_after_s: float = 60.0  # 超过这个时间强制退出（兜底）
    drain_hooks: list[Callable[[], Awaitable[None]]] = field(default_factory=list)
    cleanup_hooks: list[Callable[[], Awaitable[None]]] = field(default_factory=list)

    def on_drain(self, hook: Callable[[], Awaitable[None]]) -> None:
        """drain 阶段执行（停接新请求时）。"""
        self.drain_hooks.append(hook)

    def on_cleanup(self, hook: Callable[[], Awaitable[None]]) -> None:
        """cleanup 阶段执行（在途完成后，关闭连接前）。"""
        self.cleanup_hooks.append(hook)

    async def graceful_shutdown(self) -> None:
        """SIGTERM 处理 + 整体生命周期推进。"""
        # 1. 切换状态到 DRAINING（健康检查 ready 返回 503）
        self.state = ShutdownState.DRAINING
        await asyncio.gather(*[h() for h in self.drain_hooks])

        # 2. 等待在途请求完成（带超时）
        try:
            async with asyncio.timeout(self.drain_timeout_s):
                await self.wait_in_flight_zero()
        except TimeoutError:
            logger.warning("lifecycle.drain_timeout_exceeded", extra={...})

        # 3. cleanup（checkpoint flush / DB pool close / OSS flush）
        self.state = ShutdownState.STOPPING
        await asyncio.gather(*[h() for h in self.cleanup_hooks], return_exceptions=True)

        logger.info("lifecycle.shutdown_complete")
```

### 4.3 Timeout 嵌套 API

```python
# packages/expert-work-common/src/expert_work/common/deadline.py
@asynccontextmanager
async def with_deadline(layer: str, max_ms: float) -> AsyncIterator[DeadlineContext]:
    """派生子层 deadline；如果父层已有 deadline，自动剪裁。"""
    parent = current_deadline.get()  # contextvar
    ctx = parent.derived(layer, max_ms) if parent else DeadlineContext.root(max_ms, layer)
    token = current_deadline.set(ctx)
    try:
        yield ctx
    finally:
        current_deadline.reset(token)


async def deadline_check() -> None:
    """业务代码任意位置可调用；deadline 到期 raise DeadlineExceededError。"""
    ctx = current_deadline.get()
    if ctx and ctx.remaining_ms() <= 0:
        raise DeadlineExceededError(layer=ctx.layer)
```

---

## 5. 算法 / 关键决策

### 5.1 TLS 端到端策略

| 链路 | M0 | M1+ |
|------|----|-----|
| 外部用户 → Control Plane | 自签证书 + 浏览器警告页 (dev) / **阿里云证书** (staging/prod) | 同 M0；CDN 终结 TLS |
| Control Plane → Orchestrator | **mTLS**（静态证书，7d 有效）| cert-manager 自动轮换，1h TTL |
| Orchestrator → Sandbox Supervisor | mTLS 同上 | 同上 |
| Orchestrator → Postgres | TLS 强制（`sslmode=require`）+ RDS 提供 CA | + verify-full |
| Orchestrator → Vault / KMS Secrets Manager | TLS 强制 | 同 |
| Orchestrator → OSS | HTTPS 强制 | 同 |
| Orchestrator → MCP server | M0 仅 HTTPS（无 mTLS）| 按 server 决定 mTLS |
| Sandbox 内 → Credential Proxy | **CONNECT over HTTP**（localhost）| 同 |

**关键决策**：M0 用**静态 7 天 mTLS 证书**：CA 私钥放 阿里云 KMS，每周一手工 / 半自动签发。这是已知技术债，M1 cert-manager + SPIRE 上来后清理。原因：M0 单人项目，运维 PKI 全自动栈成本太高。

**Cipher 策略**：
- 最低 TLS 1.2，推荐 1.3
- 禁用：RC4 / 3DES / MD5 / SHA1 / NULL ciphers
- 允许：`TLS_AES_128_GCM_SHA256` / `TLS_AES_256_GCM_SHA384` / `TLS_CHACHA20_POLY1305_SHA256`（TLS 1.3）+ ECDHE+AES-GCM（TLS 1.2 兜底）
- CI 静态检查：`environments/*.yaml` 中 `tls.min_version >= 1.2`

### 5.2 Health Check 设计

| Probe | 检查范围 | 超时 | 频率 | 重启触发 |
|-------|---------|------|------|---------|
| `/healthz/live` | 自身进程响应 + 关键 goroutine 活跃 | 1s | 10s | 连续 3 次失败 → SIGTERM → SIGKILL |
| `/healthz/ready` | live + DB + Redis + Vault + （服务特定）| 5s | 10s | 不触发重启，仅摘节点 |
| `/healthz/startup` | DB migrations + warmup 完成 | 5s | 5s | 启动期专用；完成后停止 probe |

**依赖健康降级策略**（`/healthz/ready` 返回 `degraded` vs `not_ready`）：

| 服务 | 依赖 | 不可用时 |
|------|------|---------|
| Control Plane | Postgres | `not_ready`（无 DB 无法读 manifest）|
| Control Plane | Redis | `degraded`（限流退化为内存计数）|
| Control Plane | Keycloak | `degraded`（JWT 验签退化为缓存的公钥）|
| Orchestrator | Postgres | `not_ready`（无 checkpoint 无法跑）|
| Orchestrator | Anthropic API | `degraded`（fallback 到 OpenAI）|
| Orchestrator | OSS | `degraded`（uploads / snapshot 暂存内存 + 异步 retry）|
| Sandbox Supervisor | Docker daemon | `not_ready`（无 sandbox 无法服务）|

**关键决策**：**`/healthz/live` 必须无依赖** — 只检查自身 main loop 活跃。否则 DB 挂时整个集群被 k8s 误杀，雪上加霜。

### 5.3 Graceful Shutdown 流程

```
SIGTERM 收到
   │
   ▼ (t=0)
1. 状态 STARTING/RUNNING → DRAINING
   - /healthz/ready 立即返回 503
   - drain_hooks 执行：
     * Control Plane: 停 FastAPI accept；返回 503 给新请求
     * Orchestrator: 停 worker 接新 task；当前 graph step 完成
     * Sandbox Supervisor: 停 sandbox acquire；当前 sandbox 继续跑完
   │
   ▼ (t=0..drain_timeout_s)
2. wait_in_flight_zero() 轮询
   - in_flight_requests gauge → 0
   - 超时（默认 30s）→ 强制进入 cleanup（记 warning）
   │
   ▼
3. 状态 DRAINING → STOPPING
   - cleanup_hooks 执行：
     * Orchestrator: 刷 checkpoint（PostgresSaver）
     * 所有服务: close DB pool / Redis pool / HTTP client
     * 所有服务: emit "lifecycle.shutdown_complete" log
   │
   ▼ (t < force_kill_after_s, 默认 60s)
4. 正常退出 (exit 0)
   │
   ▼ (兜底 t=force_kill_after_s)
5. 信号自杀 / 外部 SIGKILL
```

**关键决策**：
- `drain_timeout_s = 30s`（默认；Orchestrator 设大些到 120s，因为单 step LLM call 可能 60s）
- `force_kill_after_s = 60s`（默认；Orchestrator 设 180s）
- Docker / k8s 的 `stopGracePeriodSeconds` 必须 ≥ `force_kill_after_s + 10s`
- **in-flight 计数** 由 FastAPI middleware 维护；每个 worker tick 也算 in-flight

### 5.4 Timeout Hierarchy（5 层嵌套）

```
┌─────────────────────────────────────────────────────────┐
│ Layer 1: Request                                        │
│  - 外部 HTTP 请求总超时：60s（健康检查不嵌入此层）       │
│  - Control Plane API 默认值                              │
│  └─┬───────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────┐
│ Layer 2: Session                                        │
│  - 单 session run 总超时：manifest 配置；默认 300s        │
│  - Orchestrator 控制                                     │
│  └─┬───────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────┐
│ Layer 3: Step (LangGraph node)                          │
│  - 单 graph step 超时：60s 默认                          │
│  - 包含一次 LLM call + 0~N 个 tool 调用                  │
│  └─┬───────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────┐
│ Layer 4: Tool                                           │
│  - 单 tool 调用超时：每 tool 在 manifest 声明           │
│    builtin: 10s / http: 30s / mcp: 30s / sandbox: 60s   │
│  └─┬───────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────┐
│ Layer 5: LLM call                                       │
│  - 单 LLM API 调用超时：30s 默认                         │
│  - 含 streaming first-token timeout = 5s 子层           │
└─────────────────────────────────────────────────────────┘
```

**嵌套不变量**：`layer[N+1].deadline_ms ≤ layer[N].deadline_ms`（自动剪裁，见 §3.3）

**Cancellation 传播**：
- 用户客户端断开 → FastAPI cancellation → `cancel_token.set()`
- 任何层在 await 点检查 `deadline_check()` 或 `cancel_token.cancelled`
- LLM stream / sandbox exec / HTTP client 都接收 cancel signal（async with cancel scope）
- **特别**：Sandbox 收到 cancel → SIGKILL container（[14 Sandbox Pool](./14-sandbox-pool.md) F.7）

**关键决策**：**deadline 是绝对时间戳，不是相对超时**。原因：跨服务传递时（gRPC / HTTP header），相对 timeout 会因网络延迟累积失真；绝对时间戳精确（前提：NTP 强制）。

### 5.5 Timeout Header 跨服务传播

```
HTTP request:
  X-Expert-Work-Deadline-Ms: 1714915380123  ← 绝对 unix ms
  X-Expert-Work-Cancel-Token: <uuid>        ← 取消时 PATCH 此 token
  traceparent: 00-<trace>-<span>-01    ← W3C
```

接收方：
- 解析 `X-Expert-Work-Deadline-Ms` → 创建 root DeadlineContext
- 如果本地处理预计超过 deadline → 立即返回 408 + log `deadline.preempted`

---

## 6. 失败模式 & 缓解

| 失败模式 | 影响 | 缓解 |
|---------|------|------|
| `/healthz/live` 阻塞（写错代码查了 DB）| k8s 误杀整个集群 | 静态分析 CI lint：live handler 禁止 import DB / Redis client |
| Graceful shutdown 超时但容器没退出 | k8s SIGKILL 强杀 → 在途请求 500 | `force_kill_after_s` 设合理；`drain_timeout_s` 给 LLM 留余地（180s）|
| 客户端断开但 cancel signal 没传到 sandbox | sandbox 浪费资源继续跑 | F.7 sandbox kill + deadline_check 在 LangGraph 每个 node 入口 |
| 时钟漂移导致 deadline 误判 | 请求过早被 cancel | M0 强制 chrony；M1 关键比较加 2s 容差 |
| TLS 证书 7 天忘续 | 服务间通信断 | 提前 2 天告警；M1 cert-manager 解决 |
| `/healthz/ready` 抖动（依赖一会儿好一会儿坏）| LB 节点反复摘挂 | 5s 滑窗，连续 3 次同状态才切换 |
| 启动慢（pg migration）但 startup probe 没等够 | k8s 重启循环 | startup probe `failureThreshold=30 × periodSeconds=5s` = 150s 启动窗 |
| 深度嵌套调用都自带 timeout 互相覆盖 | 复杂排查 | DeadlineContext 强制传 `.derived()`，禁止裸 `asyncio.timeout()` —— CI lint |
| Sandbox in-flight 计数泄漏（崩溃前没 -1）| drain 永远不到 0 | 包装为 `async with InFlightGuard():` 上下文管理器，finally 必减 |
| Postgres 连接池在 shutdown 时未关 | 残留连接占满 RDS slot | cleanup_hook 必含 `pool.close()`；CI 集成测试覆盖 |

---

## 7. 可观测性

> 命名遵循 [20 § 5.2](./20-observability.md)。

### 7.1 Metrics

```
expert_work_health_status{service,probe,result}                          counter
expert_work_health_check_duration_seconds{service,probe,dep}             histogram
expert_work_lifecycle_state{service,state}                               gauge      # state=starting/running/draining/stopping
expert_work_drain_duration_seconds{service,outcome}                      histogram  # outcome=clean/timeout
expert_work_inflight_requests{service}                                   gauge
expert_work_deadline_exceeded_total{layer}                               counter
expert_work_deadline_preempted_total{layer,upstream}                     counter    # 收到上游过期 deadline
expert_work_cancel_propagation_total{layer,sink}                         counter    # sink=sandbox|llm|tool|other
expert_work_tls_handshake_failure_total{peer,reason}                     counter
expert_work_tls_cert_expiry_seconds{peer}                                gauge      # 负值即过期
```

### 7.2 Spans

- `expert_work.lifecycle.startup` — startup hook 总耗时
- `expert_work.lifecycle.shutdown` — graceful shutdown 总耗时；attrs 含 `drain_duration_ms`, `cleanup_duration_ms`, `force_killed=bool`
- `expert_work.deadline.preempted` — deadline 提前到期，attrs 含 `layer`, `remaining_at_preempt_ms`
- `expert_work.tls.handshake_fail` — TLS 握手失败，attrs 含 `peer`, `cert_subject`, `reason`

### 7.3 Logs（事件 ID）

```
lifecycle.startup_complete
lifecycle.drain_started
lifecycle.drain_timeout_exceeded
lifecycle.shutdown_complete
health.check_failed (dep=postgres, reason=connect_refused, ...)
deadline.exceeded (layer=tool, remaining_ms=0, ...)
deadline.preempted (layer=request, upstream_deadline_ms=..., ...)
tls.handshake_failed (peer=keycloak, reason=cert_expired, ...)
```

### 7.4 告警

| 告警 | 触发条件 | 级别 |
|------|---------|------|
| `LifecycleDrainTimeout` | `expert_work_drain_duration_seconds{outcome="timeout"}` >= 1 / 10min | P1 |
| `HealthCheckFlapping` | `/healthz/ready` 1 分钟内 5 次状态切换 | P2 |
| `TLSCertExpiringSoon` | `expert_work_tls_cert_expiry_seconds < 86400 * 2` | P1（2 天）/ P0（< 6h） |
| `DeadlineExceededHighRate` | `rate(expert_work_deadline_exceeded_total[5m]) > 0.05 * rate(expert_work_inflight_requests)` | P1 |

---

## 8. 安全考虑

| 攻击面 | 防御 |
|--------|------|
| Health endpoint 暴露内部信息 | `/healthz/*` 不返回 stack trace / DB DSN / 版本号；只回状态码 + 简短 status |
| TLS 1.0/1.1 降级攻击 | 服务端 min_version=TLS 1.2；CI 检测；Nginx / FastAPI 拒绝低版本 |
| 跨服务用过期 deadline 注入越权 | deadline 来自上游 HTTP header，可被伪造 — 不依赖 deadline 做权限决策（权限完全由 AuthZ 决定）|
| Cancel token 被伪造取消他人请求 | cancel_token 由 server 生成 + 与 session 绑定；外部 PATCH 需带 session owner 权限 |
| 慢 attack 利用 long timeout | timeout 上限按 manifest tier 配置；攻击 tier 单 LLM call 不超 30s |
| Shutdown 期间敏感日志泄漏 | cleanup hook 不打印 secret / token；redactor 在 shutdown 仍生效 |

---

## 9. M0 / M1 / M2 演进

### M0（Stream A 内）

- [ ] `expert_work.common.health` + 3 个 endpoint，每个服务挂上
- [ ] `expert_work.common.lifecycle` + drain/cleanup hooks
- [ ] `expert_work.common.deadline` + DeadlineContext + with_deadline + deadline_check
- [ ] FastAPI middleware 自动注入 root DeadlineContext + in-flight counter
- [ ] HTTP client 自动透传 `X-Expert-Work-Deadline-Ms`、`X-Expert-Work-Cancel-Token`
- [ ] TLS 静态证书：脚本 `tools/tls/gen-mtls-bundle.sh` 生成 7d CA + service certs
- [ ] CI lint：live handler 不能引依赖；ruff 自定义规则
- [ ] 单元测试：lifecycle 状态机、deadline 嵌套剪裁、cancel 传播

### M1

- [ ] cert-manager 接入；mTLS 自动 1h 轮换
- [ ] SPIRE workload identity（替代静态 SAN）
- [ ] Sandbox cancel → SIGKILL 端到端集成测试（chaos 杀 sandbox in flight）
- [ ] Deadline budget alerts（按 service / tenant 维度）

### M2

- [ ] 多 region：deadline 包含地理路由 hint（远端调用预留更长 RTT）
- [ ] 智能 force_kill：根据 last checkpoint 时间动态延长 grace
- [ ] cert rotation 演练（chaos 主动撤销证书）

---

## 10. 开放问题

1. **`/healthz/ready` 与 LB 摘节点延迟**：阿里云 SLB 默认 5s 健康检查间隔，drain 后 5s 内仍可能收到新请求。需要在 FastAPI app 层加 503 兜底（任何 RUNNING 之外的状态返回 503），不仅靠 LB。
2. **Sandbox cancel 的语义边界**：用户取消 session，但 sandbox 正在执行已经写到本地文件的命令 — 是否回滚？倾向：不回滚，但 sandbox 在 cancel 后立即 SIGKILL，文件由 sandbox 池清理。
3. **Drain timeout 与 SLO 冲突**：drain 30s 意味着部署过程中可能丢失 30s 流量。倾向：M1 蓝绿部署，新版本完全 ready 后再开始 drain 旧版本。
4. **TLS 1.3 是否强制**：阿里云 RDS PostgreSQL 默认支持 TLS 1.2；TLS 1.3 部分版本支持。先放 1.2 为底线，1.3 推荐。
5. **client 端的 cancel propagation**：浏览器关闭 tab，FastAPI 收到 cancellation 后能否真传到 sandbox？需要测试 ASGI / Starlette 行为；不确定 fallback。
