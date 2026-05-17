# Stream I — 部署与发布闭环（设计先行）

> 落实 [docs/ITERATION-PLAN.md](../ITERATION-PLAN.md) § Stream I。
> 承接 Phase 0.3 已建的 baseline CI/CD + 三环境配置框架，把它生产化。
> 执行 [architecture/07-INFRASTRUCTURE-GAPS](../architecture/07-INFRASTRUCTURE-GAPS.md) §10 的 M0 子集。
>
> **本文档当前只覆盖 I.1（服务容器化 + 全栈 compose）**。I.2 – I.4（发布策略 / 回滚 / 三环境部署文档）的设计在进入对应子项前补写 —— 见 § 6。

设计先行规则（[memory:feedback_design_first_iteration.md](../../.claude/projects/-Users-mac-src-github-jone-qian-helix-agent/memory/feedback_design_first_iteration.md)）：
所有架构 / 接口 / Mini-ADR 必须在编码前锁定，I.1 的 PR 仅执行本文档。

> **背景**：Stream F.10 已给 credential-proxy 写了正式多阶段 uv 构建 Dockerfile，作为 pilot 确立"helix 服务容器化"pattern（Mini-ADR F-15）。I.1 把该 pattern 复用到其余服务，并把 M0 完整应用栈拼进一条 `docker compose up`。

---

## 1. 范围 & 边界

### 1.1 In-scope（I.1）

| 子项 | 实现内容 | 关联 |
|------|---------|------|
| **I.1a 服务容器化 + 全栈 compose** | 给 control-plane、sandbox-supervisor 各写多阶段 uv 构建 Dockerfile（复用 F.10 pattern）；`infra/docker-compose.yml` 加 `migrate`（一次性 `alembic upgrade head`）、`control-plane`、`sandbox-supervisor` 三个服务 + `full` profile —— `docker compose --profile full up` 起 M0 完整应用栈。 | Mini-ADR I-1 I-2 I-3 |
| **I.1b 全栈 egress 端到端测试** | 测试矩阵 #60：`exec_python` → sandbox →（仅）真 credential-proxy → mock upstream 全链路通。原属 Stream F.11，因需 proxy + postgres + 迁移全栈而移入 I.1（STREAM-F-DESIGN § 1.1 F.11）。 | 测试矩阵 #60 |

> I.1a / I.1b 拆两个 PR：I.1b 的 e2e 依赖 I.1a 的全栈 compose 就位。

### 1.2 Out-of-scope（明确推迟）

| 推迟项 | 落地 | 备注 |
|-------|------|------|
| orchestrator 拆独立服务 + 独立镜像 | M1（若 in-process 单体撑不住再拆）| Mini-ADR I-1 —— M0 orchestrator 是库，随 control-plane 镜像发布 |
| sandbox-supervisor 真 DinD / K8s Pod + RuntimeClass=gvisor | M3 | Mini-ADR I-2 —— M0 用 docker-out-of-docker（挂宿主 socket）|
| audit-backup-worker / retention-cleanup-job 容器化 | I.1 之外（按需）| 二者是 cron 式 job，非 M0 在线栈常驻服务，不在"全栈 compose up"目标内 |
| 蓝绿 + 金丝雀发布脚本 | I.2 | P0 #32 |
| 一键回滚 + DB 兼容 | I.3 | P0 #33 |
| 三环境（dev/staging/prod）部署文档 | I.4 | — |
| 镜像 build cache + 私有 registry push、Trivy/cosign CI gate | M1-A | M0 本地 `docker build` |

### 1.3 验收（I.1 Exit）

1. `docker compose --profile full up` 在干净环境把 M0 应用栈全部拉起，所有服务 healthcheck 转 healthy。
2. control-plane 镜像、sandbox-supervisor 镜像均多阶段构建、non-root、只携带 `.venv`。
3. 测试矩阵 #60 全栈 egress e2e 在 `Test (integration)` job 绿。
4. ITERATION-PLAN I.1 checklist 全勾，文字与实现一致。

---

## 2. 架构

### 2.1 M0 容器镜像清单

M0 在线栈 = **3 个 helix 服务镜像** + 数据层（用社区镜像，不自建）：

| 镜像 | 服务 | 入口 | 端口 | 状态 |
|------|------|------|------|------|
| `helix-control-plane` | control-plane（FastAPI）| `uvicorn control_plane.main:app` | 8000 | I.1 新建 |
| `helix-sandbox-supervisor` | sandbox-supervisor（FastAPI）| `uvicorn sandbox_supervisor.main:app` | 8000 | I.1 新建 |
| `helix-credential-proxy` | credential-proxy（aiohttp）| `python -m credential_proxy.main` | 8080 | F.10 已建 |

> **orchestrator 不在表内** —— 它是纯库（`agent_factory` / `graph_builder` / `runner` / `tools`，无 `main` / `app`），control-plane 把它当 workspace 依赖装进自己的镜像里 in-process 跑（STREAM-E-DESIGN § 2.6）。详见 Mini-ADR I-1。
>
> `helix-sandbox`（沙盒执行镜像，`infra/sandbox-image/`）不是 compose 服务 —— 它由 sandbox-supervisor 在运行期 `docker run` 启。全栈 `compose up` 前需在宿主预构建（见 § 2.4）。

### 2.2 容器化 pattern（复用 F.10）

每个 helix 服务镜像统一两阶段：

- **builder** `ghcr.io/astral-sh/uv:python3.12-bookworm-slim`：`COPY pyproject.toml uv.lock packages/ services/` → `uv sync --frozen --no-dev --no-editable --package <pkg>` —— 整个 workspace 在场供 uv 解析，但 `--package` 只构建目标服务 + 其 workspace 依赖，装成非可编辑 wheel 进自包含 `.venv`。
- **runtime** `python:3.12-slim-bookworm`：non-root 服务账户，`COPY --from=builder /app/.venv`，`ENTRYPOINT` 直接起服务。

构建均从仓库根：`docker build -f services/<svc>/Dockerfile -t helix-<svc> .`

sandbox-supervisor 比 pilot 多一步：它要 shell out `docker` CLI（`CliDockerClient`），故 runtime 阶段从 `docker:cli` 镜像 `COPY` 一个静态 `docker` 客户端二进制 —— 见 Mini-ADR I-2。

### 2.3 全栈 compose 拓扑

```
                       helix-postgres ──┐ (5432 direct)
                              │         │
                       helix-pgbouncer  │ (6432 txn pool)
                              │         │
   ┌──────────────────────────┼─────────┼────────────────┐
   │                          │         │                │
helix-control-plane    helix-redis  helix-sandbox-     helix-credential-
  :8000 (host)         (quota)      supervisor :8000    proxy :8080
   │  exec_python tool                │ docker.sock      │
   │  ──HTTP──────────────────────────┘ (DooD)           │
   │                                   │ docker run      │
   │                                   ▼                 │
   │                            helix-sandbox 容器 ──────┘
   │                            (join helix-sandbox-egress, --internal)
   └─ migrate (一次性, alembic upgrade head; 上述服务 depends_on 它完成)
```

- **数据层**（postgres / pgbouncer / redis / minio）：无 profile，恒起。
- **`full` profile**：`migrate`、`control-plane`、`sandbox-supervisor`、`credential-proxy` 同入 `full`（credential-proxy 同时保留既有 `sandbox` profile）。`docker compose --profile full up` = M0 完整应用栈。
- **migrate**：一次性服务，跑 `alembic upgrade head` 后退出；control-plane / sandbox-supervisor / credential-proxy 经 `depends_on: { migrate: { condition: service_completed_successfully } }` 等它完成 —— schema 先于服务就绪，无 race。
- **control-plane**：经 pgbouncer（6432）连库；redis 作 quota/限流后端；`HELIX_AGENT_SANDBOX_SUPERVISOR_URL=http://sandbox-supervisor:8000` 接 `exec_python`；端口 8000 映射宿主。
- **sandbox-supervisor**：直连 postgres（5432，settings 默认即如此）；挂 `/var/run/docker.sock`；沙盒由它 `docker run` 启并 join `helix-sandbox-egress`。
- **网络**：`helix-sandbox-egress`（`--internal`，F.9 Mini-ADR F-14 已建）只连 sandbox ↔ credential-proxy；其余服务走 `default` 网。credential-proxy 双归属（F.10 已建）。

详见 Mini-ADR I-3。

### 2.4 `helix-sandbox` 镜像预构建

sandbox-supervisor 运行期才 `docker run helix-sandbox:dev`，compose 不会替它构建。全栈 `compose up` 前需：

```
docker build -f infra/sandbox-image/Dockerfile -t helix-sandbox:dev infra/sandbox-image
```

在 `infra/README.md` 与 compose 服务注释中文档化此前置步骤（M0 无 Makefile，不为单条命令引入构建工具）。

### 2.5 失败模式

| 失败场景 | 处理 |
|---------|------|
| migrate 失败（schema 迁移报错）| `migrate` 退出码非 0 → `depends_on` 的 `service_completed_successfully` 不满足 → 依赖服务不启动，`compose up` 显式失败，不会出现"服务起来了但表不存在" |
| 宿主未预构建 `helix-sandbox:dev` | sandbox-supervisor 自身能起（healthcheck 看 8000 监听）；首次 `exec_python` 时 supervisor `docker run` 报 image not found → `exec_python` 工具返回明确错误。文档化预构建步骤兜底 |
| 宿主 `docker` 组 GID 与容器内不匹配 | sandbox-supervisor compose 服务用 `user: root` 跑 —— 挂 socket 已等价宿主 root，容器内 user 不构成实质差异（Mini-ADR I-2）；规避 GID 不可移植 |
| control-plane 先于 sandbox-supervisor 就绪 | `exec_python` 才需要 supervisor；control-plane 启动不 `depends_on` supervisor 的 healthy（仅松依赖），首个 run 触发时若 supervisor 未就绪则该 HTTP 调用失败并返回工具错误，不影响 control-plane 自身存活 |

---

## 3. Mini-ADR

### Mini-ADR I-1：M0 容器镜像清单 = control-plane + sandbox-supervisor + credential-proxy，orchestrator 不独立成镜像

- **背景**：ITERATION-PLAN I.1 原文写"4 个 helix 服务（control-plane / orchestrator / credential-proxy / sandbox-supervisor）"多阶段构建。
- **事实**：`services/orchestrator/` 是纯库 —— 只有 `agent_factory` / `graph_builder` / `runner` / `llm` / `tools` 等模块，**无 `main.py` / `app.py` / server 入口**。STREAM-E-DESIGN § 2.6 明确 M0 是 in-process 单体：control-plane 把 `helix-agent-orchestrator` 作为 workspace 依赖，agent graph 以后台 `asyncio.Task` 在 control-plane 进程内执行，不是独立服务。
- **决策**：M0 建 **2 个新镜像**（control-plane、sandbox-supervisor），credential-proxy 镜像由 F.10 已建。orchestrator 代码随 control-plane 镜像发布，**不独立容器化**。
- **代价**：ITERATION-PLAN I.1 checklist 文字与实际偏离 → 同步修订（I.1 子项之一）。orchestrator 拆独立服务 + 独立镜像推 M1 —— 仅当 in-process 单体撑不住（如需独立扩缩容 orchestrator）才做；M0 不预先拆。

### Mini-ADR I-2：sandbox-supervisor 容器 = docker-out-of-docker（挂宿主 docker.sock）

- **背景**：sandbox-supervisor 的 `CliDockerClient` shell out `docker run -i` 启沙盒、`docker rm` 回收。supervisor 自身容器化后，需要一个 Docker daemon 可达。
- **选项**：
  - **(a) docker-out-of-docker (DooD)** —— 挂宿主 `/var/run/docker.sock`，supervisor 容器内的 `docker` CLI 直连宿主 daemon。沙盒成为 supervisor 容器的**兄弟容器**（同宿主 daemon），join `helix-sandbox-egress` 仍可达 credential-proxy（同 daemon、同网络）。
  - **(b) DinD** —— `docker:dind` sidecar 跑嵌套 daemon。
  - **(c) supervisor 不容器化** —— 跑宿主。
- **决策**：**(a) DooD**。supervisor 镜像 runtime 阶段从 `docker:<ver>-cli` 镜像 `COPY` 静态 `docker` 客户端二进制（supervisor 只需 client，不需 daemon）。compose 服务挂 `/var/run/docker.sock:/var/run/docker.sock`。
- **理由**：(1) (b) DinD 要 `--privileged` 跑嵌套 daemon、叠加存储驱动开销大；且沙盒 + `helix-sandbox-egress` 在嵌套 daemon 内、credential-proxy 在外层 daemon → 跨 daemon 网络打通复杂。(2) (c) 破坏"一条 `compose up` 起全栈"的 I.1 目标，且 dev / dogfood 单机部署不一致。(3) DooD 是 CI runner / Jenkins agent 启容器的标准做法，沙盒隔离仍由 `docker run` 的加固 flag（`--cap-drop=ALL` / `--read-only` / gVisor）保证 —— supervisor 容器是否 DooD 不削弱沙盒隔离。
- **代价**：挂 docker.sock = supervisor 容器**等价宿主 root**。这是 M0 本地 dev / 单机 dogfood 的**已知信任边界**，非新增暴露 —— supervisor 的职责本就是全权控制 Docker 启停沙盒，无论它跑在宿主还是容器里都需要这个权限。supervisor 容器内 user 是 root 还是 non-root 在 socket 已挂的语境下不构成实质安全差异；镜像仍保留 non-root `helix` 账户作镜像卫生基线，compose 服务用 `user: root` 跑以规避宿主 `docker` 组 GID 不可移植问题。M1 转 K8s + RuntimeClass=gvisor 后此 ADR 失效（M3 roadmap 已列 sandbox 转 K8s Pod）。

### Mini-ADR I-3：全栈 compose 拓扑 —— `full` profile + 一次性 `migrate` 服务

- **背景**：`infra/docker-compose.yml` 现有数据层（无 profile，恒起）+ nginx（`proxy` profile）/ keycloak（`auth`）/ credential-proxy（`sandbox`）。I.1 要把 control-plane + sandbox-supervisor 接进来，并让一条命令起 M0 完整栈。
- **决策**：
  1. **`migrate` 一次性服务**：用 control-plane 镜像跑 `alembic upgrade head` 后退出（迁移在 `packages/helix-persistence/migrations/`，已在该镜像依赖闭包内）。control-plane / sandbox-supervisor / credential-proxy 经 `depends_on: { migrate: { condition: service_completed_successfully } }` 等它完成。
  2. **`full` profile**：`migrate` / `control-plane` / `sandbox-supervisor` / `credential-proxy` 都标 `profiles: ["full"]`；credential-proxy 同时保留既有 `["sandbox"]`（数组并列）。`docker compose --profile full up` = M0 完整应用栈。
  3. control-plane 连 pgbouncer（6432）、redis（quota）；env `HELIX_AGENT_SANDBOX_SUPERVISOR_URL` 指向 `http://sandbox-supervisor:8000`。sandbox-supervisor 直连 postgres（5432）+ 挂 docker.sock。
- **理由**：(1) 一次性 migrate + `service_completed_successfully` 是 compose 跑 schema 迁移的标准姿势 —— 避免每个服务各自 `alembic upgrade` 竞争、避免"服务起来表没建"。复用 control-plane 镜像跑迁移，不额外建 migrate 镜像。(2) `full` profile 与既有 `proxy` / `auth` / `sandbox` 一致 —— 数据层集成测试的默认 `up` 不受影响（不会因 I.1 多绑端口 / 多拉镜像而变慢）。
- **代价**：profile 组合略多（`proxy` / `auth` / `sandbox` / `full`）；在 compose 文件注释里给出 profile 速查表。

---

## 4. Verification

| 验证 | 手段 |
|------|------|
| 全栈拉起 | `docker compose --profile full up` → `docker compose ps` 所有服务 healthy（migrate 显示 exited 0）|
| 镜像合规 | control-plane / sandbox-supervisor 镜像：多阶段、non-root entrypoint、`docker history` 不含构建期工具链 |
| #60 egress e2e | `Test (integration)` job 跑 `test_fullstack_egress_e2e.py`（I.1b）|
| 单元回归 | `uv run pytest -m "not integration" -q` 全绿（I.1 不碰 Python 源码，预期零回归）|
| lint / 类型 | `ruff check` + `ruff format --check` + `pre-commit run --files <改动>` + CI-scope `mypy` |
| CI | 8/8（`Test (integration)` 首次跑全栈 e2e）|

### 测试矩阵（接续 Stream F，F 收于 #59）

| # | 用例 | 子项 | 层级 | 说明 |
|---|------|------|------|------|
| 60 | 全栈 egress e2e | I.1b | integration | `docker compose --profile full up` 起栈 → 触发声明 `exec_python` 的 agent run → 沙盒内代码经 credential-proxy 出网到 mock upstream → 断言：(a) mock upstream 收到请求且凭据头由 proxy 注入；(b) 沙盒拿到响应；(c) `credential_proxy_audit` 落 ref+host+status、**无明文 secret**。原属 Stream F.11，移入 I.1 |

> **#60 设计要点**（实现在 I.1b PR 细化）：新增 `mock-upstream` compose 服务（极简 HTTP echo，挂 `default` 网供 credential-proxy 出网可达）；测试 fixture 经 compose 起 `full` profile 栈 + 在 DB 种 `secret_allowlist` / secret；测试经 control-plane run API 触发 agent。e2e 走非 gating `Test (integration)` job。

---

## 5. PR 顺序

| PR | 内容 | 验证 |
|----|------|------|
| **I.1a** | 本设计文档；control-plane / sandbox-supervisor Dockerfile；compose 加 `migrate` + 两服务 + `full` profile；ITERATION-PLAN I.1 修订；`infra/README` 文档化 sandbox 镜像预构建 | `docker compose --profile full up` 全栈 healthy |
| **I.1b** | `mock-upstream` compose 服务；`test_fullstack_egress_e2e.py`（#60）；测试矩阵勾 #60 | #60 在 `Test (integration)` job 绿 |

---

## 6. I.2 – I.4 设计占位

I.2（蓝绿 + 金丝雀）/ I.3（一键回滚 + DB 兼容）/ I.4（三环境部署文档）在进入对应子项前，按设计先行规则在本文档增补 § 7 / § 8 / § 9。I.1 PR 不涉及。
