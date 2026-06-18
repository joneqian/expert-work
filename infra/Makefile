# Helix-Agent local dev stack — convenience wrapper over docker compose.
#
# 封装常用的「全量 dev 起栈」命令:三个 profile(full/auth/observability)的
# 必需服务一把拉起,且**排除 control-plane-green**(它与 sandbox-supervisor
# 抢 host 8001,见 docs/runbooks 的端口冲突说明)。
#
# 用法:
#   cd infra
#   make dev-up        # 起全栈(admin-ui 能跑所需的一切)
#   make dev-ps        # 看状态
#   make dev-down      # 停(留容器+卷)
#   make dev-clean     # 删容器+网络(留数据卷)
#   make langfuse-reset  # 重置 langfuse 账号/project(改 INIT 值后用)
#   make dev-info      # 打印各 web 地址 + dev 登录凭据

COMPOSE  := docker compose
PROFILES := --profile full --profile auth --profile observability

# 数据层 + 应用(不含 green) + langfuse 栈。keycloak 单列,见 dev-up 注释。
DEV_DATA     := postgres pgbouncer redis minio
DEV_APP      := migrate control-plane-blue sandbox-supervisor credential-proxy
DEV_LANGFUSE := langfuse-postgres langfuse-clickhouse langfuse-redis langfuse-minio-init langfuse-worker langfuse-web
# --wait 安全的服务集(全部带可靠 healthcheck / 一次性退出)。
WAIT_SERVICES := $(DEV_DATA) $(DEV_APP) $(DEV_LANGFUSE)

.DEFAULT_GOAL := help

.PHONY: help dev-up dev-down dev-clean dev-ps dev-logs dev-build dev-bootstrap-admin dev-reset-admin dev-seed-keycloak langfuse-reset dev-info

help: ## 列出可用目标
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
	  | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

dev-build: ## 重建 control-plane 镜像(改了后端代码后用;缓存命中则秒级)
	$(COMPOSE) $(PROFILES) build control-plane-blue

dev-up: dev-build ## 起全量 dev 栈(full+auth+observability,排除 green)
	# 先 --wait 起带可靠 healthcheck 的服务集。
	$(COMPOSE) $(PROFILES) up -d --wait $(WAIT_SERVICES)
	# keycloak 单独起、不 --wait:它的 healthcheck 4s 超时会误报 unhealthy
	# (实际可用),--wait 会卡死。约 30s 后 :8080 就绪。
	$(COMPOSE) $(PROFILES) up -d keycloak
	# seed Keycloak admin client secret 进金库:/setup 向导 + 邀请流建真账号时
	# 要用它取 KC admin token(prod 走 KMS)。幂等;非致命。
	@$(MAKE) -s dev-seed-keycloak || echo "⚠ seed-keycloak 失败,栈仍可用;就绪后手动: make dev-seed-keycloak"
	# 与生产一致:不自动建管理员。首个 system_admin 走 /setup 向导
	# (见 dev-info);要快路径仍可 `make dev-bootstrap-admin`(提权 dev 用户)。
	@echo ""
	@$(MAKE) -s dev-info

# dev 用户的 Keycloak id 在 realm 导入时随机生成(realm json 未固定),所以
# 查一次而非硬编码。client_credentials 用 api-internal 服务账号(带 view-users)。
# bootstrap_admin 在容器内跑:用容器的 HELIX_AGENT_DB_DSN(pgbouncer)+ 已装包,
# 免 host 的 uv/DB 端口依赖。幂等(重复跑 OK)。dev-up 不再自动调它(与生产
# 一致走 /setup 向导);这是想跳过向导时的快路径 / break-glass。
dev-bootstrap-admin: ## 提权 dev 用户为 system_admin(快路径;首选 /setup 向导)
	@echo "bootstrap-admin: 等 Keycloak 就绪 + 取 dev 用户 id..."
	@SUB=""; for i in $$(seq 1 30); do \
	  SA=$$(curl -sf http://localhost:8080/realms/helix-agent/protocol/openid-connect/token \
	    -d grant_type=client_credentials -d client_id=helix-agent-api-internal \
	    -d client_secret=dev-internal-secret-rotate-me 2>/dev/null \
	    | python3 -c 'import sys,json; print(json.load(sys.stdin)["access_token"])' 2>/dev/null) || { sleep 2; continue; }; \
	  SUB=$$(curl -sf "http://localhost:8080/admin/realms/helix-agent/users?username=dev&exact=true" \
	    -H "Authorization: Bearer $$SA" 2>/dev/null \
	    | python3 -c 'import sys,json; print(json.load(sys.stdin)[0]["id"])' 2>/dev/null) || { sleep 2; continue; }; \
	  [ -n "$$SUB" ] && break; sleep 2; \
	done; \
	if [ -z "$$SUB" ]; then echo "FAILED: 取不到 dev 用户 id(Keycloak 未就绪?)"; exit 1; fi; \
	echo "dev subject_id = $$SUB"; \
	$(COMPOSE) $(PROFILES) exec -T control-plane-blue python -m control_plane.bootstrap_admin --subject-id $$SUB

# 只删平台 system_admin 绑定 → 回到零-admin,让 /setup 向导重新可用。
# 不动 LLM key(密钥库)/租户/agent/记忆/审计 —— 仅清授权,数据保留。
# role_binding 是 FORCE-RLS;容器内 postgres 超级用户直删可绕 RLS。
dev-seed-keycloak: ## seed Keycloak admin client secret 进金库(幂等;/setup + 邀请流需要)
	$(COMPOSE) $(PROFILES) exec -T control-plane-blue \
	  python -m control_plane.seed_keycloak_secret --value dev-internal-secret-rotate-me

dev-reset-admin: ## 清平台管理员绑定(保留其它数据;还原 /setup 向导)
	$(COMPOSE) $(PROFILES) exec -T postgres \
	  sh -c 'psql -U "$$POSTGRES_USER" -d "$$POSTGRES_DB" -c "DELETE FROM role_binding WHERE platform_scope = true;"'
	@echo "── /setup 状态 ───────────────────────────────────"
	@curl -s http://localhost:8000/v1/setup/status || echo "(control-plane 未就绪?)"
	@echo ""
	@echo "→ 开 admin-ui 走 /setup 向导(initialized 应为 false)"

dev-down: ## 停所有 dev 服务(留容器 + 数据卷)
	$(COMPOSE) $(PROFILES) stop $(WAIT_SERVICES) keycloak

dev-clean: ## 删容器 + 网络(留数据卷;比 stop 更彻底)
	$(COMPOSE) $(PROFILES) down

dev-ps: ## 看 helix 容器状态
	@docker ps --filter "name=helix" --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'

# 用法:make dev-logs SVC=control-plane-blue
SVC ?= control-plane-blue
dev-logs: ## tail 某服务日志(SVC=<service>,默认 control-plane-blue)
	$(COMPOSE) $(PROFILES) logs -f $(SVC)

langfuse-reset: ## 重置 langfuse 账号/project(改 LANGFUSE_INIT_* 或 .env 后)
	# LANGFUSE_INIT_* 仅首次建库跑;改值需清 postgres 卷 + flush redis 认证缓存
	# (只清 postgres 会残留旧 key→旧 project 缓存,API 返空)。
	$(COMPOSE) stop langfuse-web langfuse-worker langfuse-postgres
	-$(COMPOSE) rm -f langfuse-postgres
	-docker volume rm infra_langfuse-postgres-data
	$(COMPOSE) --profile observability up -d --wait \
	  langfuse-postgres langfuse-clickhouse langfuse-redis langfuse-worker langfuse-web
	-docker exec helix-langfuse-redis redis-cli FLUSHALL
	@echo "langfuse 已重置;新 INIT 值生效"

dev-info: ## 打印各 web 地址 + dev 登录凭据
	@echo "── Helix dev 栈地址 ──────────────────────────────"
	@echo "  admin-ui      http://localhost:5173   (单独 pnpm dev 起;SSO 登录)"
	@echo "  control-plane http://localhost:8000   (后端 API)"
	@echo "  Langfuse      http://localhost:3001   (trace 调试)"
	@echo "  Keycloak      http://localhost:8080   (IdP;约 30s 就绪)"
	@echo "  MinIO         http://localhost:9001   (对象存储控制台)"
	@echo "── 首次:建第一个平台管理员(与生产一致)────────────"
	@echo "  开 admin-ui → 自动进 /setup 向导 → 填邮箱/密码 + Setup Token"
	@echo "  Setup Token   infra/.env 的 HELIX_AGENT_SETUP_TOKEN(默认 dev-setup-token)"
	@echo "  邮箱用新地址(别用 dev@helix.local,Keycloak 已占)"
	@echo "  快路径(跳向导): make dev-bootstrap-admin  (提权 dev 用户)"
	@echo "── 其它登录 ──────────────────────────────────────"
	@echo "  Keycloak 管理 admin / admin_dev"
	@echo "  Langfuse      见 LANGFUSE_INIT_USER_* / .env"
