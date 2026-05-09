# 14 Sandbox Pool — gVisor 容器池化与配额仲裁

> 把"每次跑 Agent 都新建容器"升级为"按 image 维度维持 warm pool + 镜像构建缓存 + tenant 配额仲裁 + 驱逐策略"，让 sandbox 冷启动 P95 从秒级降到 < 500ms，并保证多租户公平。

---

## 1. 职责 & 边界

### ✅ 做
- gVisor / Docker 容器生命周期管理（CREATE / READY / IN_USE / CLEANING / DESTROYED）
- **每个 image 维度的 warm pool**（idle 容器池），acquire / release 接口
- **镜像构建缓存**（base image + requirements.txt → layer key），避免重复构建
- **tenant 配额仲裁**：按 `tenant_quota.max_sandboxes` 接受 / 拒绝 / 排队
- **驱逐策略**：LRU + idle_timeout + 强制清理（OOM、不健康、image 过期）
- 节点资源调度（M1：单机 bin-packing；M2：K8s scheduler 接管）
- `isolation_level=dedicated_node` 的节点亲和 / 反亲和

### ❌ 不做
- 容器内的工具调用与命令执行 → 由 Sandbox Supervisor + LangGraph ToolNode
- 凭证注入 → 由 [11 Credential Proxy](./11-credential-proxy.md)
- 网络出站策略 → 由 [21 Network Policy](./21-network-policy.md)
- 镜像签名验证 → 由 [18 Manifest 供应链](./18-manifest-supply-chain.md)
- 命令的语义安全检查（sandbox_audit）→ 由 Orchestrator middleware 在调用前过滤

---

## 2. 上下游依赖

| 依赖方向 | 子系统 | 关系 |
|---------|--------|------|
| 上游调用方 | Orchestrator | 调 `acquire(tenant, image, resources)` 获取 sandbox |
| 上游调用方 | Sandbox Supervisor | 长会话内复用同一 sandbox（thread→sandbox 绑定）|
| 下游 | Docker Engine / containerd + runsc | OCI runtime |
| 下游 | 私有镜像 registry | image build 产物 push |
| 横切 | [15 AuthN/AuthZ](./15-authn-authz.md) | 验证 acquire 调用方身份与 tenant scope |
| 横切 | [16 Quota / Rate Limit](./16-quota-rate-limit.md) | sandbox 实例数维度配额 |
| 横切 | [17 Audit Log](./17-audit-log.md) | 配额拒绝、强制清理写审计 |
| 横切 | [20 Observability](./20-observability.md) | 池水位、acquire 延迟、build 命中率 metric |

---

## 3. 数据模型 / 状态机

### 3.1 状态机

```
              acquire (cache hit)
   ┌────────────────────────────────────────┐
   │                                        ▼
CREATING ──ready──▶ READY ──acquire──▶ IN_USE ──release──▶ CLEANING ──ok──▶ READY (回池)
   │                  │                    │                                  │
   │                  │                    │                                  │idle > T
   │                  │                    │                                  ▼
   └─error──▶ FAILED  └─idle/evict─▶ DESTROYED ◀──force_kill──── (任意状态) ──┘
```

- **CREATING**：拉镜像 / 启动容器 / 健康检查
- **READY**：在 warm pool 内 idle，等待 acquire
- **IN_USE**：被某个 thread 占用
- **CLEANING**：会话结束，清理 /workspace、reset 进程
- **FAILED / DESTROYED**：终态

### 3.2 Postgres DDL

```sql
CREATE TABLE sandbox_image (
  id            BIGSERIAL PRIMARY KEY,
  tenant_id     TEXT NOT NULL,                -- '__shared__' 表示通用 base image
  image_ref     TEXT NOT NULL,                -- registry/repo:tag
  layer_key     TEXT NOT NULL,                -- sha256(base + requirements.txt + copy_set)
  size_bytes    BIGINT,
  built_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_used_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  build_status  TEXT NOT NULL,                -- BUILDING / READY / FAILED
  build_log_url TEXT,
  UNIQUE (tenant_id, layer_key)
);
CREATE INDEX ON sandbox_image (tenant_id, last_used_at);

CREATE TABLE sandbox_instance (
  id              UUID PRIMARY KEY,
  tenant_id       TEXT NOT NULL,
  image_layer_key TEXT NOT NULL,
  node            TEXT NOT NULL,              -- 主机标识；K8s 时 = pod name
  container_id    TEXT NOT NULL,              -- docker / containerd id
  state           TEXT NOT NULL,              -- CREATING/READY/IN_USE/CLEANING/DESTROYED/FAILED
  isolation_level TEXT NOT NULL,              -- shared / dedicated_sandbox / dedicated_node
  thread_id       TEXT,                       -- 仅 IN_USE 时非空
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  acquired_at     TIMESTAMPTZ,
  released_at     TIMESTAMPTZ,
  destroyed_at    TIMESTAMPTZ,
  cpu_quota       NUMERIC(4,2),               -- 例 1.5 = 1.5 vCPU
  memory_bytes    BIGINT,
  evict_reason    TEXT                        -- idle_timeout / oom / unhealthy / image_outdated / force
);
CREATE INDEX ON sandbox_instance (tenant_id, image_layer_key, state);
CREATE INDEX ON sandbox_instance (state, released_at);  -- LRU 驱逐
```

### 3.3 Pydantic schema（acquire 请求 / 响应）

```python
class AcquireRequest(BaseModel):
    tenant: str
    image_ref: str
    layer_key: str
    isolation_level: Literal["shared", "dedicated_sandbox", "dedicated_node"]
    resources: ResourceSpec                # cpu / memory / pids
    thread_id: str
    timeout_s: int = 30                    # acquire 自身超时（不同于 sandbox lifetime）
    purpose: Literal["production", "eval"] = "production"
    # M2 eval sandbox 走独立 pool 时启用

class AcquireResponse(BaseModel):
    sandbox_id: UUID
    node: str
    endpoint: str                          # 内网 HTTP 控制端点
    cold_start: bool                       # warm pool 命中 = False
    acquired_at: datetime
```

---

## 4. 关键接口

### 4.1 Python（包内 API）

```python
class SandboxPool:
    async def acquire(self, req: AcquireRequest) -> AcquireResponse: ...
    async def release(self, sandbox_id: UUID, *, reset: bool = True) -> None: ...
    async def force_destroy(self, sandbox_id: UUID, reason: str) -> None: ...
    async def get_pool_state(self, image_layer_key: str) -> PoolState: ...

class ImageBuilder:
    async def ensure(self, spec: ImageBuildSpec) -> SandboxImage:
        """幂等：layer_key 已 READY 直接返回；否则触发 build。"""
```

### 4.2 HTTP API（内网，仅 Orchestrator / Supervisor 可调）

```
POST /v1/sandboxes:acquire        Body: AcquireRequest        → AcquireResponse
POST /v1/sandboxes/{id}:release   Body: {"reset": true}       → 204
POST /v1/sandboxes/{id}:destroy   Body: {"reason": "..."}     → 204
GET  /v1/pool/{layer_key}/state                                → PoolState
POST /v1/images:build             Body: ImageBuildSpec        → 202 + build_id
```

所有调用都要带 `X-Helix-Tenant` header，由 [15 AuthN/AuthZ](./15-authn-authz.md) 验证后才到本服务。

---

## 5. 算法 / 关键决策

### 5.1 Warm pool 大小

**决策**：M1 起每个 `(tenant, layer_key)` 维度独立 pool，参数：

```
target = ceil(qps_15min * avg_lifetime_s / 60) * 1.5
size   = clamp(target, min=2, max=20)
```

每 30s 重算一次。**关键决策**：用 EWMA 平滑 qps（α=0.3），避免抖动；扩容立即拉、缩容延迟 5min（避免 thrashing）。

### 5.2 acquire 算法

```
1. AuthN/AuthZ 通过 → 进入
2. 查 quota：tenant 已用 sandbox 数 < tenant_quota.max_sandboxes ?
   否 → 429（Retry-After 由 quota 子系统返回）
3. isolation_level == dedicated_node ?
   是 → 走独占节点亲和算法（找空闲节点；无则等待 / 拒绝）
4. 查 warm pool ((tenant, layer_key)) → 有 idle → 标记 IN_USE 返回（cold_start=False）
5. 无 idle → ImageBuilder.ensure(layer_key) → docker run → 健康检查 → 返回（cold_start=True）
6. 全程加 Postgres advisory lock(hash(tenant, layer_key)) 防并发膨胀
```

### 5.3 镜像构建缓存

**决策**：layer_key = `sha256(base_image_digest || sorted(requirements.txt) || sorted(copy_set))`。

- base_image_digest：base image 的 immutable digest（不是 tag）
- requirements.txt：行级排序后哈希（避免顺序差异穿透缓存）
- copy_set：所有 `COPY src dst` 的 (src_sha256, dst) 对

构建产物 push 私有 registry，Postgres 记录 `sandbox_image` 行。同 layer_key 第二次 build 直接命中 → 0 build 时间。

### 5.4 驱逐策略

按优先级强制清理：

1. **强制（force_destroy）**：OOM kill、健康检查失败、image_outdated（layer_key 已被新版本替换）
2. **idle_timeout**：state=READY 且 `now - released_at > idle_timeout`（默认 5min），LRU 顺序
3. **配额抢占**：tenant A 已超 soft quota 且其他 tenant 排队 acquire → 强制释放 A 的 idle 实例

每 10s 跑一次 reaper job（pg_cron 或 arq scheduled）。

### 5.5 macOS dev 限制

**关键决策**：dev 环境 docker 默认 runtime（gVisor 不支持 macOS）；prod Linux 强制 runsc：

```yaml
# docker-compose.dev.yml
services:
  sandbox:
    runtime: runc            # macOS / Linux dev 都用默认
# helm/prod values.yaml
sandbox:
  runtimeClassName: gvisor   # K8s 用 RuntimeClass=gvisor
  fallback: kata             # M3 高敏租户
```

代码层提供 `SandboxRuntimeProvider` 抽象，按环境注入。

### 5.6 Image 加固清单

所有 sandbox image 强制以下要求（M0 起）：

- 非 root 用户（`USER agent`，uid=10000）
- `--read-only` 根文件系统（`/workspace` 和 `/tmp` 是 tmpfs / writable mount）
- `--cap-drop=ALL`（无 capabilities）
- `--security-opt=no-new-privileges`
- 不安装 `sudo`、`su`、编译器（除非 manifest 明确声明）
- 启动 entrypoint 无 shell 注入空间（exec form ENTRYPOINT）

---

## 6. 失败模式 & 缓解

| 失败模式 | 影响 | 缓解 |
|---------|------|------|
| 镜像 build 失败 | acquire 阻塞 | 同步返回 503；`sandbox_image.build_status=FAILED` 缓存 5min 防重试风暴 |
| 节点资源耗尽（CPU/mem） | acquire 拒绝 | bin-packing 扫多节点；全失败 → 429 Retry-After |
| Docker daemon 卡死 | 整节点不可用 | health probe 失败 → 节点移出调度池 + 告警 |
| Pool 容器健康检查超时 | 误标 IN_USE | TTL 兜底：state=IN_USE 且 `now - acquired_at > sandbox.timeout_s + 30s` 强制 destroy |
| LRU 驱逐误杀热点 | 频繁冷启动 | 用 last_acquired_at 而非 last_released_at，过滤刚 acquire 的 |
| Tenant 配额绕过（伪造 header） | 单租户耗尽资源 | acquire 必须带 JWT，由 AuthN 验证 tenant ↔ JWT 一致 |
| 镜像 supply chain 污染 | sandbox 内被植入恶意代码 | image push 前 cosign 签名（[18](./18-manifest-supply-chain.md)）；pull 时 verify |
| 节点宕机时持有大量 IN_USE | session 全挂 | 节点心跳 30s 无响应 → 全部 IN_USE 标记 FAILED；orchestrator 收到 502 重试到其他节点 |
| Build 缓存命中歧义（不同 base image 同 requirements） | 跑错版本 | layer_key 包含 base_image_digest（immutable）|
| dedicated_node 资源浪费 | 利用率低 | 该 tenant 多 thread 共享同节点；空闲超 30min 释放 |

---

## 7. 可观测性

> 日志完整字段遵循 [20 § 5.3](./20-observability.md)。

### 7.1 Prometheus metric

```
helix_sandbox_pool_size{tenant,layer_key,state="ready"}              gauge
helix_sandbox_pool_size{tenant,layer_key,state="in_use"}             gauge
helix_sandbox_acquire_total{tenant,layer_key,result="cache_hit|cold_start|reject"}  counter
helix_sandbox_acquire_latency_seconds{tenant,result}                 histogram
helix_sandbox_lifetime_seconds{tenant,layer_key}                     histogram
helix_sandbox_evict_total{tenant,reason}                             counter
helix_image_build_total{result="hit|build_ok|build_fail"}            counter
helix_image_build_duration_seconds                                   histogram
```

**SLO（M1 目标）**：`acquire_latency_seconds{result="cache_hit"}` P95 < 500ms；`acquire_latency_seconds{result="cold_start"}` P95 < 3s；`helix_sandbox_acquire_total{result="reject"}` rate < 0.1%。

### 7.2 OTel span

- `sandbox.acquire`（attrs：tenant, agent_name, agent_version, layer_key, isolation_level, cold_start, queue_depth）
- `sandbox.image.ensure`（attrs：tenant, agent_name, agent_version, layer_key, cache_hit, build_duration_ms）
- `sandbox.evict`（attrs：tenant, agent_name, agent_version, sandbox_id, reason）

---

## 8. 安全考虑

| 攻击面 | 防御 |
|--------|------|
| 镜像供应链污染 | cosign 签名 + 私有 registry + pull verify（详见 [18](./18-manifest-supply-chain.md)）|
| 跨租户 sandbox 复用（cache poison）| pool key 必须包含 tenant，绝不跨 tenant 共享 IN_USE/READY 实例 |
| Sandbox 逃逸（runc CVE）| 全部走 gVisor（用户态 syscall 拦截）；M3 高敏 tenant 用 Kata |
| 资源耗尽 DoS | tenant 维度 max_sandboxes + 全局 max_per_node + cgroup 隔离 |
| 信息泄漏（残留 /workspace）| release 时强制 reset：tmpfs umount + 重新 mount；写入磁盘的内容随容器销毁 |
| 镜像层泄漏 secret | image build 禁止 `COPY .env`、build_args 不落镜像；CI 扫描镜像 |
| 配额绕过（伪造 tenant header）| AuthN 阶段强制对齐 JWT tenant 与 X-Tenant header |
| Reaper 误杀 IN_USE | reaper 只看 state=READY 且 idle 超阈值；IN_USE 走独立 TTL |

**关键决策**：`isolation_level=dedicated_node` 时强制独占节点，节点上不调度其他 tenant 的 sandbox；通过节点 label `helix.io/tenant=<id>` + scheduler taint 实现。

---

## 9. M0 / M1 / M2 演进

### M0（4-6 周）—— 冷启动版
- 不做 warm pool，每次 `docker run`（gVisor）
- ImageBuilder：本地 docker build；无 layer 缓存（首版接受 1-3s 冷启动）
- 单节点；`sandbox_instance` 表已建（为 M1 铺路）
- 配额仅校验 max_sandboxes 简单计数

### M1（6-8 周）—— Warm pool + image cache
- 引入 warm pool（每 image 维度），目标 P95 < 500ms
- ImageBuilder layer cache（layer_key 算法上线）
- 多节点 bin-packing；reaper job
- `isolation_level=dedicated_sandbox`（不复用 idle）正式生效
- 配额走 [16 Quota](./16-quota-rate-limit.md) 集成

### M2（6-8 周）—— Durable + 跨节点
- sandbox 状态可 checkpoint，节点宕机后另起节点恢复
- `dedicated_node` 完整支持
- pool 自动伸缩（基于 EWMA）
- 镜像 GC：`last_used_at > 30d` 自动清理

### M3 —— K8s + Kata
- 改造为 K8s Pod + RuntimeClass=gvisor / kata
- pool 由 Operator 管理（CRD: `SandboxPool`）
- Pod scheduler 接管节点选择
- 跨集群 federation（M3 末）

---

## 10. 开放问题

1. **warm pool 跨 thread 复用是否安全**：同一 (tenant, layer_key) 的两个 thread 复用同一 idle sandbox，是否需要"reset 到出厂"？目前方案：tmpfs 重挂 + 强制 process kill。需用渗透测试验证。
2. **镜像 GC 触发点**：是按 last_used_at 还是按租户 image 总数 quota？M2 决定。
3. **K8s 阶段 pool 实现**：Operator + CRD vs Deployment + autoscaler？倾向前者（更精细控制 ready→in_use 状态机）。
4. **dedicated_node 利用率**：高隔离客户付费溢价是否覆盖空闲成本？需产品定价配合。
5. **build 集中式 vs 节点本地**：远程 buildkit + push registry 还是节点本地 build？倾向远程（方便缓存共享）。
