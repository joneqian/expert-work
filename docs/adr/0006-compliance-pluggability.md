# ADR-0006：合规可插拔架构 — `compliance_pack` 字段语义

- **状态**：✅ 已决策
- **日期**：2026-05-11
- **决策依据**：Expert Work 是多业务线引擎（医疗 / HR / 客服 / R&D），不同业务合规要求不同；引擎必须**业务无关**，合规通过**租户级配置**驱动而非硬编码
- **背景**：M0 Stream C.7（租户级配置隔离）+ D.2（PII redactor）+ D.3（retention TTL）+ Stream F（沙盒隔离级别）都需要按租户驱动

---

## TL;DR

每个租户在 `tenant_config` 里声明 `compliance_pack: hipaa | gdpr | sox | none | custom`。引擎读到 pack 名后**自动注入**：
- PII redactor 配置（`pii_fields` 列表）
- 加密策略（at-rest / in-transit / 字段级）
- 数据保留 TTL（event_log / audit_log / memory / upload 各自）
- 沙盒隔离级别（shared / dedicated_sandbox / dedicated_node）
- 数据驻留（region）
- 审计粒度

业务代码 / Agent manifest **完全无需** 关心合规细节。

---

## 1. 上下文

### 多业务多合规的痛点

| 业务 | 典型合规 | 关键约束 |
|------|---------|---------|
| 医疗 | HIPAA / 等保三级 | patient.id_card / patient.diagnosis 等字段必须 redact + 加密 + 专用沙盒 |
| HR | GDPR / 个保法 | employee.salary / employee.id 须 redact + 数据驻留 + 30 天 retention 上限 |
| 客服 | 个保法 | customer.phone / customer.address redact；2 年 retention |
| 研发工具（内部） | 无 | 仅默认日志 redact 即可 |

如果把这些规则硬编码到 Orchestrator / Audit 服务，每加一个业务都要改引擎 → 走 Dify 老路。

### P0 关联

- P0 #7 PII 脱敏框架（per-tenant `pii_fields`）— 本 ADR 让它**真正可插拔**
- P0 #8 数据保留策略
- P0 #20 / #21 租户级配置
- P0 合规子项（M2 落地，但 M0 必须留接口）

---

## 2. 决策

### 2.1 配置入口

```yaml
# tenant_config (Postgres tenants 表 jsonb 字段)
tenant_id: 550e8400-...
name: "Medical Team A"
compliance_pack: hipaa            # 单一字符串字段，引擎据此自动注入
custom_overrides:                  # 可选；hipaa 默认值之上的覆盖
  retention:
    event_log_days: 30             # 比 hipaa 默认更严
```

`compliance_pack` 的合法值由引擎注册表定义，初版支持 4 个内置 pack：

| Pack | 默认 PII fields | retention | 沙盒隔离 | 加密 |
|------|---------------|-----------|---------|------|
| **none** | 无 | event_log 90d / audit 7y / memory 30d | shared | TDE + TLS |
| **hipaa** | patient.* / mrn / dob / id_card | event_log 365d / audit 7y / memory 90d | dedicated_sandbox | TDE + TLS + 字段级加密 |
| **gdpr** | personal_data / email / phone / ip | event_log 30d（自动 erasure 触发器）| shared | TDE + TLS |
| **sox** | financial_record / account | event_log 7y | dedicated_sandbox | TDE + TLS |

### 2.2 引擎注入机制

加载 manifest + tenant_config 时：

```python
# pseudo code, lands in packages/expert-work-runtime/src/expert_work/runtime/compliance/
def resolve_runtime_config(manifest, tenant_config) -> RuntimeConfig:
    pack = COMPLIANCE_PACKS[tenant_config.compliance_pack]
    config = pack.defaults()
    if tenant_config.custom_overrides:
        config = merge(config, tenant_config.custom_overrides)
    # 强制约束：custom_overrides 不能放宽 pack 默认（比如 hipaa 下不能把 retention 调短）
    validate_no_loosening(pack, config)
    return config
```

注入位置：
- **PII redactor middleware** 读 `config.pii_fields`，应用到所有 outgoing payload
- **Stream A.4 audit_log 写入** 读 `config.retention.audit_log_years`，由归档 job 消费
- **Stream F.4 sandbox 池** 读 `config.sandbox_isolation`，决定从 shared / dedicated 池子取
- **Stream G'.8 event_log 归档** 读 retention 字段

### 2.3 Pack 注册表

`packages/expert-work-runtime/src/expert_work/runtime/compliance/packs/`：
- `none.py`、`hipaa.py`、`gdpr.py`、`sox.py` 各自一个文件，导出 `Pack` 实例
- 注册中心 `__init__.py` 暴露 `COMPLIANCE_PACKS` dict
- 新增 pack：加一个文件 + register；零引擎核心改动

### 2.4 验证 / 测试矩阵

每个 pack 上线前必须跑：
- [ ] PII redactor 拦截声明字段（input + log + Langfuse trace）
- [ ] retention 自动清理 job 按 pack 时长跑
- [ ] 沙盒隔离级别正确（shared 不会拿到 dedicated 池资源，反之亦然）
- [ ] custom_overrides 不能放宽（loosening 必须 reject + 告警）

---

## 3. 后果

### 正向

- **业务无关引擎**：核心代码不含合规分支判断；合规只在 pack 文件里
- **新业务零核心改动**：医疗团队上线只需 `compliance_pack: hipaa`
- **审计可追溯**：tenant_config 是 source of truth，任何合规行为可在 audit_log 找到 pack 名
- **可扩展**：未来加 PCI / 等保四级 等 pack 只是新增文件

### 负向 / 风险

- **pack 设计错误成本高**：错配 retention / encryption 会泄漏数据
  - 缓解：每个 pack 上 prod 前必须有 E2E 测试 + 合规团队 sign-off（M2 形式化）
- **custom_overrides 的"不能放宽"逻辑复杂**：每个字段都要有方向（数值类：仅允许更严；布尔类：仅允许 true→false 不允许反向）
  - 缓解：用 Pydantic + 单元测试覆盖
- **跨 pack 共享租户的场景模糊**：本 ADR 假设 1 租户 1 pack；多业务混合租户场景留 M2 再设计

---

## 4. 备选方案

| 方案 | 否决理由 |
|------|---------|
| **业务代码自己处理合规** | 引擎不再业务无关；新业务都要改核心；走 Dify 老路 |
| **完全声明式（在 manifest 里写 retention 等字段）** | 业务方需了解合规细节；权责颠倒；不可执行 |
| **合规作为 plugin 包**（Python 插槽）| 比内置 pack 重；M0 不需要；M2 evaluate |
| **每租户一个微服务实例**（物理隔离） | 资源浪费；多租户引擎价值丧失 |

---

## 5. 落地引用

- **Stream A** vendor DeerFlow 时为 `tenants` 表预留 `compliance_pack` + `custom_overrides` JSONB 字段
- **Stream C.7** 租户级配置隔离落 `compliance_pack` 读取路径
- **Stream D.2** PII redactor 读 pack-driven `pii_fields`
- **Stream D.3** retention TTL job 读 pack 配置
- **Stream F** sandbox 池读 `sandbox_isolation`
- **Stream G'.8** event_log 归档读 retention
- 新增 pack 的 PR 模板：`docs/templates/compliance-pack.md`（M0 后期补）
