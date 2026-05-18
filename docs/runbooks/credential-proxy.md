# Runbook — Credential Proxy

> Stream G.3 故障预案。credential-proxy（Stream F.5）是沙盒出站的唯一放行点：
> 沙盒经它 `POST /forward` 出网，proxy 注入凭据后转发到 upstream。
> 它故障 = 沙盒内代码无法访问外部 API（但不影响不出网的 `exec_python` 调用）。

## 故障现象

- 沙盒内代码出网请求失败（连不上 `credential-proxy:8080` / 5xx）。
- `credential_proxy_audit` 表大量 `status` 非 `ok`（`denied` / `secret_miss`）。
- `/forward` 返回 4xx：allowlist 未命中或 secret 解析失败。

## 诊断

1. **存活**：`docker compose ps credential-proxy`；端口 8080 监听。
2. **审计表**：
   `SELECT status, count(*) FROM credential_proxy_audit GROUP BY status;`
   —— `denied` 多 = allowlist 问题；`secret_miss` 多 = SecretStore 问题。
3. **allowlist**：`secret_allowlist` 表是否有对应
   `(tenant, agent, version, secret_ref)` 行 —— 缺行则 proxy 拒绝注入。
4. **SecretStore**：M0 `local_dev` 后端读 env 文件
   （`HELIX_CRED_PROXY_SECRET_STORE_ENV_FILE`）；prod 走阿里云 KMS（ADR-0007）。
   `secret_miss` 查该后端是否有对应 ref。
5. **网络**：proxy 双归属 `helix-sandbox-egress`（沙盒侧）+ `default`（出网侧）；
   出网失败查 `default` 网到 upstream 的连通性。
6. **DB**：proxy 读 `secret_allowlist` + 写 `credential_proxy_audit` —— DB 故障
   见 [postgres.md](./postgres.md)。

## 处置

- **容器不健康**：`docker compose restart credential-proxy`（无状态，安全）。
- **`denied` 飙升**：确认是否漏配 `secret_allowlist` 行 —— 业务新增 secret 用法时
  需先登记 allowlist（manifest 加载校验）。
- **`secret_miss` 飙升**：SecretStore 后端缺 secret 或 key 失效；
  prod 在 KMS 补 / 轮换，短 TTL 缓存（60s）过期后自动重拉。
- **绝不**为排障打印明文 secret —— proxy 设计上 `credential_proxy_audit`
  只记 `secret_ref` + `target_host` + `status`，不落明文（安全约束）。

## 回滚

proxy 镜像无状态，回滚 = 部署上一版镜像。
故障由 allowlist / secret 配置变更引入 → 回滚该配置。

## 升级

沙盒出网失败属 P2（不影响纯 LLM agent 与不出网的沙盒调用）；
若伴随 DB 故障 → 转 [postgres.md](./postgres.md) 按 P0 处理。
