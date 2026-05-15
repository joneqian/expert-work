# ADR-0007：应用 Secret 存储 — 阿里云 KMS Secrets Manager（M0）+ 评估 Vault（M1+）

- **状态**：✅ 已决策（M0 阶段；M1 重新评估）
- **日期**：2026-05-11
- **决策依据**：Phase 0.1 决策 3 已选阿里云全套基础设施；M0 单人项目优先选托管服务降低运维负担；Vault 动态密钥 / Engine 等高级能力 M0 用不到，M1 再做迁移决策
- **背景**：Phase 0.1 决策 3 把 Secret 存储留作 ADR；本 ADR 完成该决策

---

## TL;DR

- **M0**：用 **阿里云 KMS Secrets Manager**（托管 secret 服务）作为应用 secret 后端
- **抽象层**：所有应用代码通过 `helix_agent.runtime.secrets.SecretStore` 接口访问，**不直接绑 阿里云 SDK**；切换后端零代码改动
- **M1 触发条件 重新评估 Vault 自托管**：
  - 动态数据库凭证需求（短 TTL 自动轮换）
  - 跨云 / 离线部署需求
  - PKI / 证书签发集成需求（mTLS 自动轮换）
  - 任一触发 → 启动 Vault 迁移 ADR

---

## 1. 上下文

### 需求

| 场景 | 数据 | M0 频率 |
|------|------|--------|
| LLM provider API key | Anthropic / OpenAI key | 启动加载 + 30 天轮换 |
| 数据库密码 | Postgres / Redis 密码 | 启动加载 |
| 第三方 webhook secret | Slack / GitLab / 业务方提供 | 按 manifest 引用 |
| Service-to-service 凭据 | mTLS 客户端证书 | 静态，M1 转 cert-manager |
| Agent 工具凭据 | 由 Credential Proxy 管理（Stream F.5） | 运行时按需注入 |

### P0 关联

- P0 #2 / #3 服务认证 / API Key — Secret 存储是底层依赖
- P0 #9 加密策略 — secret at-rest 必须 KMS 加密
- P0 #29 连接池 — secret 读取必须 cache，不能每请求查后端

### 决策约束

- 已选阿里云全套（Phase 0.1 决策 3）
- 国内合规，secret 数据不出境
- 单人项目，**M0 阶段尽量减少自托管基础设施**

---

## 2. 决策

### 2.1 后端 — 阿里云 KMS Secrets Manager（M0）

**为什么**：
- 与 ADR-0004 (OSS)、ADR-0005 (其余托管基础设施) 决策一致
- 阿里云 RAM 集成 → 服务通过 RAM Role 读 secret，无需再分发凭据
- 自动加密（用 KMS 主密钥）
- 自动版本管理 + soft delete + 审计日志（写入阿里云 ActionTrail）
- 零运维（HA、备份、跨 AZ 由阿里云保障）

**用法**：
- 应用启动时 `actiontrail-aliyun-secrets:GetSecretValue` 读取
- TTL cache（短 TTL，比如 1 小时）避免频繁查
- secret 命名约定：`helix-agent/{env}/{service}/{key}`

**引用 URI（F.6 落地时定稿）**：manifest / tenant_config 不嵌 secret 值,只嵌**引用** —— 规范 scheme 是 `secret://<name>`,`<name>` 即上面的命名约定路径,由 `SecretStore.get(name)` 解析。`secret://` 刻意**后端无关**(manifest 不该知道后端是 KMS / Vault / dev .env)。Stream C 设计文里写的 `kms://` 泄漏了后端,F.6 的 `parse_secret_ref` 把它作为**兼容别名**接受(解析到同一 name),但 `secret://` 是规范形式,`kms://` 应迁移。

### 2.2 抽象层接口

```python
# packages/helix-runtime/src/helix_agent/runtime/secret_store/base.py
from typing import Protocol

class SecretStore(Protocol):
    async def get(self, name: str, *, version: str | None = None) -> str: ...
    async def put(self, name: str, value: str) -> None: ...  # admin-only
    async def list_versions(self, name: str) -> list[str]: ...
```

> **包目录修正（F.6 落地时）**：实际包名是 `runtime/secret_store/`,不是本 ADR 原写的 `runtime/secrets/` —— 后者会触发"`secrets/` 目录存放凭据值"的工具/权限启发式,而这里是抽象**代码**。

实现：
- `LocalDevSecretStore`（dev / test 用本地 .env 文件,无依赖）—— **F.6 已落地**
- `AliyunKmsSecretStore`（M0 生产主用）—— **follow-up**：`make_secret_store("aliyun_kms")` 暂抛 `NotImplementedError`,待生产部署时落地(含短 TTL cache),模式同 `stream_bridge` 推迟 Redis 后端
- `VaultSecretStore`（M1 评估后启用）

### 2.3 dev 环境降级方案

本地 dev 不接阿里云 — 通过 `environments/dev.yaml` 配置切换到 `LocalDevSecretStore`：
- secret 从 `.env` 文件读（被 `.gitignore` 排除）
- `.env.example` 给模板（公开提交）

### 2.4 M1 触发评估 Vault 的条件

| 触发条件 | 含义 |
|---------|------|
| **动态数据库凭证** | Vault DB Secrets Engine 自动签发短 TTL Postgres 用户名密码（生命周期与会话绑定） |
| **跨云 / 离线** | 单云 KMS 不支持的部署场景 |
| **PKI 集成** | cert-manager 用 Vault PKI 自动签发 mTLS 证书（替代静态 7 天证书） |
| **HSM 加固** | 法务 / 合规明确要求硬件 HSM 保护根密钥 |
| **应用层 Transit 加密** | 应用要求 Vault Transit Engine 做字段级加密代理 |

任一触发即启动 Vault 迁移 ADR；迁移成本预估 2-3 周（含部署 HA 集群 + 双写 + 应用切换）。

---

## 3. 后果

### 正向

- **M0 零自托管运维**：阿里云 KMS Secrets Manager 全托管
- **应用代码后端无关**：抽象层 → 切 Vault 时零应用改动
- **审计自动**：所有 secret 访问记入阿里云 ActionTrail
- **加密合规**：默认 KMS 加密 + RAM 权限分离

### 负向 / 风险

- **缺动态密钥**：当前数据库密码静态；M1 上 Vault 才能做短 TTL
- **锁定阿里云 RAM**：Service Account 概念是 阿里云 特有，跨云迁移要重做
  - 缓解：抽象层吸收差异
- **没有原生 secret 轮换"工作流"**：阿里云 KMS Secrets Manager 支持轮换，但要自己写 hook
  - 缓解：M0 接受手工轮换；M1 评估 Vault 时一起做

### 验证手段

- [ ] 启动时从 KMS Secrets Manager 拿到 `/helix-agent/dev/llm/anthropic-api-key`
- [ ] RAM Role 限定后无关 service 拿不到（403）
- [ ] secret 旋转后 cache 在 1 小时内刷新
- [ ] dev 模式从 `.env` 拿值，不接阿里云

---

## 4. 备选方案

| 方案 | 否决理由（M0）|
|------|--------------|
| **Vault 自托管 M0 就上** | M0 单人项目，HA Vault 运维负担过大；M0 用不到 Vault 高级能力 |
| **阿里云 OOS Secrets Encryption** | 比 KMS Secrets Manager 弱，无版本管理 |
| **直接放环境变量（Kubernetes Secrets）** | Kubernetes Secret 默认仅 base64，不加密；多团队访问难审计 |
| **直接放数据库表** | 反模式；与"secret 应跟应用数据隔离"原则冲突 |

---

## 5. 落地引用

- **Stream F.6（已落地）** SecretStore 抽象 + `LocalDevSecretStore` + `parse_secret_ref` + `make_secret_store` 工厂：`packages/helix-runtime/src/helix_agent/runtime/secret_store/`。`AliyunKmsSecretStore` 为 follow-up（见 § 2.2）。
- **Stream F.5** Credential Proxy aiohttp 版从本抽象读后端 secret，再注入到沙盒外调
- **agent factory（Stream E follow-up）** 从 manifest `secret://` 引用经本抽象解析出 provider API key，装配 `LLMRouter`
- **environments/{env}.yaml** 已声明 `secrets.backend: tbd` 字段 → 实施时改为 `aliyun-kms-secrets`（prod/staging）或 `local-env`（dev）
- **`.env.example`** 模板：Stream A 实施时落地

## 6. 复审

- **M0 末**：盘点是否触发 M1 评估条件
- **M1 开始**：如有触发，启动 ADR-00XX「Secret Store 迁移：KMS Secrets Manager → Vault」
