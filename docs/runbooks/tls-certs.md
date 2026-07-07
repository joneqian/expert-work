# Runbook — TLS / mTLS 证书（Stream A.10 / C.2）

> 全链路 TLS 的证书运维预案。Expert Work 的服务间流量经 **nginx 8443 mTLS** 终止；
> nginx 校验客户端证书后注入 `X-Forwarded-Client-Cert`（XFCC），应用层
> `MTLSVerifier`（control-plane）据 Subject 比对 `mtls_allowed_service_subjects`。
> 证书过期 / 缺失 = 服务间调用握手失败（TLS handshake / 400 XFCC 校验失败）。

## 拓扑

```
service ──client cert──> nginx:8443 (ssl_verify_client on, ca.crt)
                           │  校验客户端证书 → 注入 XFCC header
                           ▼
                    control_plane_upstream  ── MTLSVerifier 比对 Subject
nginx:8080 (plain) ── /healthz 旁路（k8s liveness，无 mTLS）
```

- **TLS 终止**：`infra/nginx/nginx.conf` — `listen 8443 ssl` + `ssl_certificate server.crt` + `ssl_client_certificate ca.crt` + `ssl_verify_client on`。
- **应用层校验**：`services/control-plane/.../auth/mtls.py::MTLSVerifier`（`mtls_enabled=True`，`mtls_allowed_service_subjects` 白名单）。
- **证书挂载**：`infra/docker-compose.yml` — `./dev-certs:/etc/nginx/certs:ro`。

## dev PKI

`tools/dev-certs/generate.py` 产出至 `infra/dev-certs/`（**gitignored，dev-only，私钥不入库**）：

| 文件 | 用途 | 有效期 |
|---|---|---|
| `ca.crt` / `ca.key` | 自签根 CA | 5 年 |
| `server.crt` / `server.key` | nginx TLS server 证书 | 1 年 |
| `orchestrator.crt` / `.key` | orchestrator 客户端证书 | 1 年 |
| `sandbox-supervisor.crt` / `.key` | supervisor 客户端证书 | 1 年 |
| `control-plane.crt` / `.key` | 集成测试用（冒充 orchestrator） | 1 年 |

生成 / 重生成：

```bash
python tools/dev-certs/generate.py        # 或 python -m tools.dev_certs.generate
docker compose restart nginx              # 让 nginx 重读挂载的新证书
```

## 故障现象

- 服务间调用 TLS handshake 失败 / 连不上 `:8443`。
- nginx 日志 `SSL_do_handshake() failed` / `client certificate verify failed`。
- control-plane 返回 400 + XFCC 校验失败（Subject 不在 `mtls_allowed_service_subjects`）。

## 诊断

1. **证书有效期**（最常见——过期）：
   ```bash
   openssl x509 -in infra/dev-certs/server.crt -noout -enddate -subject
   openssl x509 -in infra/dev-certs/ca.crt     -noout -enddate
   ```
   `notAfter` 已过 → 重生成（见上）。
2. **挂载在位**：`docker compose exec nginx ls /etc/nginx/certs`（应见 `server.crt`/`server.key`/`ca.crt`）。
3. **白名单**：客户端证书 Subject CN（如 `orchestrator.expert_work.local`）须在 `mtls_allowed_service_subjects`。
4. **nginx config**：`docker compose exec nginx nginx -t`。

## 轮换 / 到期

- **dev**：server / client 证书 1 年期，CA 5 年。到期前重跑 `generate.py` + `restart nginx`。**新增服务**进 `mtls_allowed_service_subjects` 时也重跑（补客户端证书）。
- **到期监控（M1 缺口）**：当前无证书到期 metric。M1 补 `expert_work_tls_cert_expiry_seconds{cert}` gauge + 30 天告警（同 `expert_work_dr_backup_age_seconds` 形态，见 20-observability §5.2.2）。在此之前靠本 runbook 的 `openssl enddate` 人工核 + 部署日历提醒。
- **prod（非本仓 dev PKI）**：用真 CA（内部 PKI / cert-manager + Let's Encrypt）签发，私钥经 secret manager 注入，不用 `generate.py`；轮换走 cert-manager 自动续期或 KMS。

## 关联

- 设计：`subsystems/15-authn-authz.md`（mTLS / service account）、`subsystems/21-network-policy.md`。
- mTLS 应用层：`auth/mtls.py`（XFCC 解析 + Subject 比对）。
- 部署：`runbooks/deployment.md`、`runbooks/control-plane.md`。
