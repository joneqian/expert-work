# ADR-0004：对象存储选型 — 阿里云 OSS + S3 兼容抽象

- **状态**：✅ 已决策
- **日期**：2026-05-11
- **决策依据**：基础设施部署姿态选定阿里云全套（[phase-0-launch 决策 3](../decisions/phase-0-launch.md)）；OSS 提供 S3 兼容 API，应用代码可保持后端无关
- **背景**：M0 Stream A.5 对象存储抽象 + D.1 audit WORM + Stream G'.8 event_log 冷归档 + Sandbox snapshots / uploads 均依赖对象存储

---

## TL;DR

- **生产 / staging**：阿里云 OSS（S3 兼容协议）
- **本地 dev**：MinIO（S3 兼容协议，docker-compose 单容器）
- **应用层抽象**：所有代码通过 `helix_agent.runtime.storage` 接口调用，**底层用 S3 兼容 SDK**（如 `aiobotocore`），不直接依赖阿里云专有 SDK；切换后端零代码改动

---

## 1. 上下文

### 使用场景

| 场景 | 数据特征 | 保留 / 加密要求 |
|------|---------|---------------|
| **Agent 文件上传** | 用户上传的 PDF / 图片 / CSV | 标准加密；按租户 retention |
| **Sandbox snapshot** | 沙盒会话末态快照（用于 replay）| 90 天 |
| **audit_log WORM 备份** | 7 年合规副本 | OSS Object Lock（WORM 桶）|
| **event_log 冷归档** | 半年后归档；查询路径需要 | 7 年；可解归档 |
| **Agent artifacts** | manifest 历史版本、签名材料 | 长期 |

### P0 关联

- P0 #16（对象存储正式选型）— **本 ADR 直接落实**
- P0 #6（审计日志不可篡改）— 通过 OSS Object Lock（WORM）实现
- P0 #18（event_log 冷归档 pipeline）— 通过普通 OSS bucket + lifecycle policy

### 决策约束

- 已决定主体上云阿里云（Phase 0.1 决策 3）
- 国内部署，海外对象存储（AWS S3、GCS）不在选型范围
- 数据合规：必须支持 SSE-KMS（用 阿里云 KMS 加密）

---

## 2. 决策

### 2.1 后端选择

| 环境 | 实现 |
|------|------|
| **dev**（本地 docker-compose） | MinIO 单容器（S3 协议端口 9000）|
| **staging** | 阿里云 OSS bucket `helix-agent-staging`，cn-hangzhou region |
| **prod** | 阿里云 OSS bucket `helix-agent-prod`，cn-hangzhou region，SSE-KMS 加密 |

### 2.2 抽象层

所有应用代码通过统一接口操作对象存储：

```python
# packages/helix-runtime/src/helix_agent/runtime/storage/base.py

from typing import Protocol

class ObjectStore(Protocol):
    async def put(self, key: str, data: bytes, *, metadata: dict | None = None) -> None: ...
    async def get(self, key: str) -> bytes: ...
    async def delete(self, key: str) -> None: ...
    async def list_prefix(self, prefix: str) -> list[str]: ...
    async def presigned_url(self, key: str, *, expires_in: int = 3600) -> str: ...
```

实现仅一个：`S3CompatibleObjectStore`（基于 `aiobotocore`），通过 `environments/{env}.yaml` 的 `object_storage.endpoint` 配置切换。

### 2.3 Bucket / Key 设计

| Bucket 用途 | Bucket 名 | Object Lock |
|-------------|----------|-------------|
| 应用数据（uploads / snapshots / artifacts） | `helix-agent-{env}` | 关闭，依靠 lifecycle |
| audit_log WORM 备份 | `helix-agent-{env}-audit-worm` | **启用**，governance 模式，7 年 |
| event_log 冷归档 | `helix-agent-{env}-archive` | 关闭，但 lifecycle 归档到低频访问 |

Key 命名约定（多租户隔离）：

```
{tenant_id}/uploads/{session_id}/{filename}
{tenant_id}/snapshots/{thread_id}/{ts}.tar.gz
audit_worm/{year}/{month}/{day}/{batch_id}.json.gz
archive/event_log/{year}/{month}/{tenant_id}/{batch_id}.parquet
```

### 2.4 加密策略

- **at-rest**：所有 prod bucket 启用 SSE-KMS，密钥用 阿里云 KMS 管理（P0 #9）
- **in-transit**：所有客户端用 HTTPS endpoint
- **客户端额外加密**：sensitive payload（如某些 audit 字段）M1+ 可在应用层再加密一层

---

## 3. 后果

### 正向

- **零厂商锁定（应用层）**：S3 兼容协议是事实标准；从阿里云 OSS 切换到腾讯云 COS / AWS S3 / MinIO 仅需改 endpoint
- **本地开发友好**：MinIO 本地起步，无需联网，与 prod 协议一致
- **WORM 合规即用**：阿里云 OSS Object Lock 直接满足 P0 #6 7 年不可篡改要求
- **加密 + 生命周期管理由云托管**：lifecycle policy 把数据从标准存储 → 低频 → 归档（成本 -70%）

### 负向 / 风险

- **OSS 与 S3 协议有边角差异**：少数 API（multipart、bucket policy）行为不同 — 用 `aiobotocore` 通过 endpoint_url 兼容，已在多个开源项目验证可用
- **跨区域复制需另配**：M1 跨 AZ DR 时需启用 OSS 跨区域复制
- **大对象（>5GB）需要 multipart**：抽象层接口要支持 multipart upload；初版只做小对象，多 GB 大对象 M0 后期补

### 监控指标

- `object_storage_request_latency_p95`（按 operation 标签）
- `object_storage_worm_backup_lag_seconds`（P0 告警阈值 5 min）
- `object_storage_egress_bytes`（成本相关）

---

## 4. 备选方案

| 方案 | 否决理由 |
|------|---------|
| **腾讯云 COS** | 与决策 3「阿里云全套」冲突；技术上同等可行 |
| **七牛云 / 又拍云** | 国内对象存储；不在阿里云全套决策内；社区 SDK 不如阿里云丰富 |
| **MinIO 自托管 prod** | 与「云 OSS」决策冲突；运维成本更高；HA / 跨 AZ 自己搭 |
| **直接用 阿里云 OSS Python SDK（oss2）** | 应用层锁定阿里云；切换后端要改代码；S3 兼容协议是更好抽象 |

---

## 5. 落地引用

- **Stream A.5** 对象存储抽象接口 + S3 兼容实现：`packages/helix-runtime/src/helix_agent/runtime/storage/`
- **Stream A.6** Postgres 备份用 OSS 而非阿里云 RDS 自带：复用同抽象
- **Stream D.1** audit WORM bucket 配置：staging/prod yaml 中声明 worm bucket 名 + Object Lock 保留期
- **Stream G'.8** event_log 冷归档 pipeline：`tools/` 下独立 job，定期跑
- **environments/{env}.yaml** 已声明 `object_storage.backend / endpoint / region / bucket` 字段
- **本地 dev** docker-compose 添加 MinIO 容器（Stream A 实施时落地）
