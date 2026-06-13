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

.PHONY: help dev-up dev-down dev-clean dev-ps dev-logs dev-build langfuse-reset dev-info

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
	@echo ""
	@$(MAKE) -s dev-info

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
	@echo "── dev 登录 ──────────────────────────────────────"
	@echo "  admin-ui SSO  用户 dev / 密码 devpass  (realm-helix-agent.json 预置)"
	@echo "  Keycloak 管理 admin / admin_dev"
	@echo "  Langfuse      见 LANGFUSE_INIT_USER_* / .env"
