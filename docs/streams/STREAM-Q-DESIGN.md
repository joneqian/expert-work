# Stream Q — Web Key Management(平台统一密钥管理 + agent 主模型接线)(设计先行)

> 状态:设计先行(PR A)。后续 PR B–F 按本文 Mini-ADR 实现。
> 关联:延续 [STREAM-P-DESIGN](./STREAM-P-DESIGN.md)(平台配模型只存 ref);本 Stream 让 key **值**能在 web 粘贴 + 让 agent 主对话模型用上它。

---

## 0. 背景与范围(2026-05-29 用户拍板)

### 0.1 触发问题

用户问:"大模型 key 不是应该登录后在 web 上填么?为什么要写进 `infra/dev-keys/dev-llm-keys.local`?" —— 戳中真实产品 gap。

**现状(Stream P 后)的两个 gap**:

1. `/settings/platform`(Stream P PR I)+ `validate_secret_ref`(Mini-ADR P-8)只存 `secret://`/`kms://` **引用**,拒绝明文;真值要另放 secret 后端。dev 无 KMS → 落文件,且 `LocalDevSecretStore.put()` 只写内存不回写文件(重启丢)。
2. agent **主对话模型** key 走 manifest `api_key_ref` → `secret_store.get()`,**根本不经过平台配置页**(配置页只喂 embedder / reranker / web_search)。详见 [reference:agent-llm-key-resolution]。

### 0.2 目标产品形态

登录后在 web 直接粘贴真 key(像 OpenAI / Vercel 控制台)→ 后端加密存 → agent 主对话模型也从这里取 key → **E2E 全程在 web 完成**(无需碰文件)。

### 0.3 范围决策

1. **加密落库**(而非"写穿 SecretStore.put 到文件"):信封加密、教科书做法、dev 持久。实现成**新的 `SecretStore` 后端**(不在 `platform_secret` 表加裸密文列),让 `secret_store.get(ref)` 解析链一行不改。
2. **本迭代两半都做**:① 加密金库 + web 粘贴 UI;② chat-LLM 主模型接线。交付完整"登录→粘贴 key→agent 直接能跑"闭环。
3. **canonical manifest 去掉 `api_key_ref`** → 走平台 key(真正验证 web key 喂 agent);另留一个带 `api_key_ref` 的 fixture 守 override 回归。
4. **出口本迭代只开平台级**(system_admin 每 provider 一个 key)。租户级粘贴、其它 secret 类型(MCP / S3)迁进金库 = 后续按需接(后端通用故不返工)。

### 0.4 范围纪律(simplicity first)

后端做**通用 + 生产级**(密钥存储天生通用);**出口窄**(只平台 provider/tool key)。显式不做:真 KMS-wrapped KEK(等 aliyun_kms 落地)、KEK 自动轮换、租户级粘贴 UI、版本回滚端点、`ChainedSecretStore` 混合后端。

---

## 1. 关键事实(3×Explore + 2×Plan 核实,file:line)

- `SecretStore` Protocol `packages/helix-runtime/.../secret_store/base.py`:`get(name,*,version=None)` / `put(name,value)` / `list_versions(name)`;`SecretNotFoundError(SecretStoreError, KeyError)`。
- 后端:`local_dev.py`(内存 dict,`put` 不回写文件)、`aliyun_kms.py`(cache 层 stub,factory 对 "aliyun_kms" 抛 `NotImplementedError`)。factory `make_secret_store(backend="local_dev",*,env_file=)`;**`app.py:416` 硬编码 "local_dev"**,无 backend 选择设置。
- `cryptography>=46.0.7` 已是 control-plane 依赖(`AESGCM` 可用);无其它 crypto 库。
- `parse_secret_ref("secret://a/b")→"a/b"`(refs.py);scheme `secret://` + legacy `kms://`。
- `platform_*_secret` 表(migration 0049):tenant-less、无 RLS、refs-only。`validate_secret_ref`(protocol/platform_secret.py)拒明文。
- RLS 模式(0005):`ENABLE/FORCE ROW LEVEL SECURITY` + `USING (tenant_id = NULLIF(current_setting('app.tenant_id',true),'')::uuid)`;`bypass_rls_session()`(control_plane/tenant_scope.py)做 tenant-less 写。
- chat-LLM key:`build_llm_router`(orchestrator/agent_factory.py:741-793)`api_key_ref None→raise` 否则 `secret_store.get(parse_secret_ref(ref))`。`ModelSpec.api_key_ref: str|None=None`(已可空)。
- embedder/rerank 模式(control_plane/runtime.py:200-283):`Resolving*` 持 `resolver+secret_store`,`resolver.resolve_provider(tenant_id,provider)→ref→secret_store.get`。
- **接线 gap**:`make_agent_builder`(runtime.py:132-184)只收 `secret_store`,不收 resolver;`_build(spec,tenant_id=)` 收到 tenant_id 但**不转发**给 `build_agent`。`CredentialsResolver` 在 `app.state`(app.py:587)但没传进 builder。orchestrator **不可** import helix-common(已有 `mcp_allowlist_provider` 这类 callable 解耦先例)。第二个 build 点 `make_child_agent_builder`(subagent_runtime.py:45)也要接。

---

## 2. Mini-ADRs(统一 Q- 前缀)

- **Q-1** 加密落库做成 `SecretStore` 后端(`SqlEncryptedSecretStore`,非 `platform_secret` 加裸列),复用 `get/put` Protocol → 解析链零改。
- **Q-2** AES-256-GCM + 单 KEK 直接加密值 + per-row 96-bit 随机 nonce(`os.urandom(12)`)+ AAD 绑 `name`(防密文跨行搬运)。KEK = env `HELIX_AGENT_SECRET_ENCRYPTION_KEY`(base64 32B,`SecretStr`,boot 校验);`kek_version` 列留 KMS-wrap 轮换前向钩子(真 KMS = prod follow-up,aliyun_kms 未实现)。
- **Q-3** 新表 `encrypted_secret` 含 `tenant_id UUID NULL`(NULL=平台;非 NULL=租户,留后续)+ RLS policy `tenant_id IS NOT DISTINCT FROM NULLIF(current_setting('app.tenant_id',true),'')::uuid`(NULL 行 bypass 可见、租户行隔离),平台写仍走 `bypass_rls_session()`。通用后端、本迭代只平台行。
- **Q-4** 写路径:`PUT /v1/platform/credentials` 收原始 `value`(`SecretStr`,与 `secret_ref` 二选一)→ `put("helix-agent/platform/llm/<provider>", value)`(加密入 `encrypted_secret`)→ catalog(`platform_provider_secret`)存 `secret://<name>` ref(**catalog 表不改 schema**)→ `invalidate()`。re-paste = 新 version 行(`is_current` 翻转),ref 字符串不变。
- **Q-5** chat-LLM 接线:`ProviderKeyResolver = Callable[[str], Awaitable[str]]`(orchestrator 定义,唯一跨界类型);control-plane 绑 `resolver+tenant_id` 成闭包传入。`build_llm_router` 逐 entry:**manifest `api_key_ref` 优先 → 平台 fallback → 皆无 raise**。子 agent + vision 同接。`CredentialsResolverError→AgentFactoryError` 翻译在 control-plane 闭包内(orchestrator 不 import helix-common)。
- **Q-6** **P-8 姿态变更(显式)**:旧 = "密钥值绝不进 DB";新 = "**明文**绝不进任何表;`encrypted_secret` 只存 AES-GCM 密文;catalog 仍 refs-only,`validate_secret_ref` 规则不变,仍拒 `secret_ref` 字段里的明文"。
- **Q-7** 安全:`value` 全程 `SecretStr`;`put` / 日志 / 审计 detail 绝不含 key 值(审计只记 name/provider/ref/actor/kek_version);前端 `type=password` 不回显;admin 端点 TLS;中间件不 log body;GCM nonce 必 `os.urandom(12)`。
- **Q-8** canonical manifest 去 `api_key_ref` 走平台 key;另留带 `api_key_ref` 的 override fixture 守回归。

---

## 3. 数据模型 & 加密

### 3.1 `encrypted_secret` 表(migration `0050_encrypted_secret`,down_revision=`0049_platform_secrets`,id=21 字符 OK)

| 列 | 类型 | 说明 |
|----|------|------|
| `id` | UUID PK `gen_random_uuid()` | 代理键,一行一个 (name, version) |
| `tenant_id` | UUID **NULL** | NULL=平台;非 NULL=租户(留后续) |
| `name` | Text NOT NULL | `parse_secret_ref` 输出的不透明名 |
| `version` | Text NOT NULL | 版本 id,newest-first 供 `list_versions` |
| `ciphertext` | BYTEA NOT NULL | AES-256-GCM 输出(含 tag) |
| `nonce` | BYTEA NOT NULL | 12B 随机/次 |
| `kek_version` | Text NOT NULL | 哪把 KEK(`"env-v1"`),供轮换 |
| `is_current` | Bool NOT NULL | `get(name)` 返回的版本 |
| `created_at` | timestamptz | |
| `created_by` | Text NOT NULL | actor,审计 |

约束:`UNIQUE(tenant_id, name, version)`;**partial unique** 保证一个 current/(tenant,name)——⚠️ NULL tenant_id 不去重,用 `COALESCE(tenant_id, '00000000-0000-0000-0000-000000000000')` 表达式索引或 `WHERE tenant_id IS NULL` 分支(风险 #2)。RLS `ENABLE/FORCE` + Q-3 policy。无任何明文列。

### 3.2 信封加密 util

`AESGCM(kek).encrypt(nonce, value.encode(), aad=name.encode())`;`kek` = base64-decode(env)→32B,boot 校验长度(失败 fail-loud);解密对称。util 放 `helix-persistence`(或 helix-common 的 crypto 子模块),不依赖 control-plane。

---

## 4. 后端 + 接线

### 4.1 金库后端(PR B)

- 类 `SqlEncryptedSecretStore` 放 `packages/helix-persistence/.../encrypted_secret/{base,sql,memory}.py`(+ `models/encrypted_secret.py`),持 `async_sessionmaker`,镜像 `SqlPlatformSecretStore`;memory 双簿供单测/in-memory 模式。
- factory `make_secret_store` 加 `"sql_encrypted"`(注入 session_factory + kek_provider);`Literal` 加项。**推荐在 `_build_sql_stores`(app.py:1070)构造**(`_SqlStores` 加字段),`app.py:416` 按 `settings.secret_store_backend` 选择 → 避免 helix-runtime 依赖 helix-persistence。
- settings 加 `secret_store_backend: Literal["local_dev","sql_encrypted","aliyun_kms"]="local_dev"` + `secret_encryption_key: SecretStr|None`。

### 4.2 写路径(PR C)

`PUT /v1/platform/credentials`:payload 加 `value: SecretStr | None`(与 `secret_ref` 二选一)。`value` 存在时:生成名 `helix-agent/platform/llm/<provider>` → `secret_store.put(name, value)` → `upsert_provider(provider, secret_ref=f"secret://{name}", enabled=True, actor)` 在 `bypass_rls_session()` 内 → `platform_secrets_service.invalidate()`。审计 `PLATFORM_SECRET_WRITE`(若新增需 protocol + control-plane 双 Literal 同改 [memory:audit-literal-drift]),detail 不含值。

### 4.3 chat-LLM 接线(PR E)

```
orchestrator/agent_factory.py:
  ProviderKeyResolver = Callable[[str], Awaitable[str]]   # 唯一跨界类型
  build_llm_router(..., provider_key_resolver=None):
    for entry in chain:
      if entry.api_key_ref is not None:   ref = entry.api_key_ref           # manifest 优先
      elif provider_key_resolver:         ref = await provider_key_resolver(entry.provider)  # 平台 fallback
      else:                               raise AgentFactoryError(...)
      api_key = await secret_store.get(parse_secret_ref(ref))
  # 同样穿过 build_step_routers / build_agent / vision(:322)

control_plane/runtime.py:
  make_provider_key_resolver(resolver, tenant_id) -> ProviderKeyResolver   # 闭包,catch CredentialsResolverError→AgentFactoryError
  make_agent_builder(..., credentials_resolver=None): _build 内 tenant_id!=None 时构造闭包转发
control_plane/subagent_runtime.py: make_child_agent_builder 同接
app.py: :687 / :679 两处传 credentials_resolver
```

precedence:manifest override / 平台兜底 → 现有 manifest 字节级同路径(零回归)。tenant_id None(validation/preview):不构造 resolver → 行为同今天。

---

## 5. Admin UI(PR D)

`/settings/platform` provider 行编辑:加"粘贴 key"输入(`<input type="password">`,不回显已存值,占位显示"已配置/未配置")。保存 → `PUT` 带 `value`。i18n en + zh-CN 同步(TranslationKeys interface)。Storybook + Playwright + axe。

---

## 6. PR 拆分(~6 PR,每个 CI-green、零债)

- **PR A**(本)`stream-q/a-design` — 本设计文档 + ITERATION-PLAN backlog。
- **PR B** `stream-q/b-vault` — 信封加密 util + `models/encrypted_secret.py` + migration 0050 + `encrypted_secret/{base,sql,memory}.py` + factory `sql_encrypted` + settings 选择器/KEK + app.py 解硬编码 + boot 校验 + 单测。
- **PR C** `stream-q/c-write-path` — 写路径 + 审计 + 版本语义 + API 测。
- **PR D** `stream-q/d-ui` — 粘贴 key UI + i18n + Storybook/Playwright/axe。
- **PR E** `stream-q/e-wiring` — chat-LLM 接线(orchestrator + control-plane + 子 agent + app)+ 测。
- **PR F** `stream-q/f-canonical` — canonical manifest 去 api_key_ref + test 改 + override fixture + E2E 文档收口。

> 关键路径:A → B → C → D;E 可与 C/D 并行(只依赖 B 接口);F 收口(依赖 B+E)。

---

## 7. 风险

1. **KEK 丢=全量不可恢复** — env KEK 丢/误轮换 → 所有密文解不开。需 ops runbook + `kek_version` 轮换路径(PR B 文档必写)。这是 env-KEK 捷径 vs 真 KMS 的头号风险。
2. **partial unique over NULL tenant_id** — Postgres NULL 互不相等;用 `COALESCE` 表达式索引或 `WHERE tenant_id IS NULL` 分支。
3. **GCM nonce 必 `os.urandom(12)` 每次新** — 确定性派生灾难性破密。
4. **子 agent 漏接** — `make_child_agent_builder` 独立 build 点,漏了则无 api_key_ref 的子 agent build 失败。
5. **provider 名域匹配** — `resolve_provider(provider=entry.provider)` 字符串须与平台 catalog key 一致,否则静默 miss。加针对性测试。
6. **factory 签名膨胀** — 别把 SQLAlchemy 类型拉进 helix-runtime;在 app 层 `_build_sql_stores` 构造注入。
7. **明文经 API body/内存** — admin 端点 TLS、中间件不 log body、`value` 全程 `SecretStr`。
8. CI 门:[memory:audit-literal-drift] 双改、[memory:ci-lint-type-test-scopes] mypy 不含 control-plane/src + pytest `-m "not integration"`、[memory:protocol-sweep-includes-tools-eval]、alembic id ≤32。

---

## 8. Verification(本迭代完成 = web 粘贴 key,agent 真能跑)

1. PR B 单测:`put`→DB 行是密文(grep 不到明文)、`get`→原值;错 KEK→解密失败;`list_versions` 多版本;RLS 平台行 bypass 可读。
2. PR C:`PUT {provider, value:"sk-ant-…"}`→201;`GET` 反映 ref;`encrypted_secret` 有密文行;审计无值;非 admin 403。
3. PR E:`build_llm_router` 单测——有 ref 用 manifest;无则走 resolver;皆无→`AgentFactoryError`;子 agent 同。
4. PR D:Playwright——粘贴 key、保存、`type=password` 不回显;axe 过。
5. **端到端(PR F 后,需真 key + docker)**:起栈(无 dev-keys 文件)→ 登录 → `/settings/platform` 粘贴真 key → 注册无 `api_key_ref` 的 canonical manifest → Playground 发 turn → **真实回话**(key 全程只在 web 填过)。
6. 每 PR:pre-commit(含 detect-private-key)、pytest `-m "not integration"`、mypy、前端全门;push 前 preflight。

---

## 9. 后续(不在本迭代)

- 真 KMS-wrapped KEK(等 aliyun_kms `KmsBackend` 落地);KEK 轮换自动化 + runbook。
- 租户级 key 粘贴 UI / 其它 secret(MCP / S3)迁进金库(后端已通用,出口后接)。
- `ChainedSecretStore`(env-file + 加密 DB 混合);版本回滚端点(flip `is_current`)。
