# Worker 生产休眠 live-smoke 审计（2026-06-16）

> 起因：4.4 / 11.4 live E2E 发现多个 worker「CI 全绿但生产端到端休眠」（trajectory
> recorder 传 None #657、skill-evolution 启动闸用错凭证源 #656、eval 容器缺 tools/）。
> 本审计对全部 16 个后台 worker 逐一核「布线是否真接通 live 路径 + 输入数据从哪来」，
> 排出哪些需 live-smoke 验证、哪些静态已充分。方法：3 个 Explore agent 并行读真实代码 +
> app.py 接线点 + 单测驱动，我对最高信号项亲自 grep 复核。

## 休眠签名（来自 E2E 实证）

1. 启动闸用 settings env-dict 凭证，而非金库 per-tenant 解析（#656 / 本轮 #661）。
2. app.py 接线点把核心可选依赖传 `None`（#657 trajectory_recorder）→ 功能从不触发。
3. 处理后不标记 consumed → 每 tick 无限重处理（#658）。
4. 容器镜像缺运行期文件（#652 tools/）。
5. worker 依赖只有 live agent run 才产生的数据；in-process 单测用 seeded 数据绕过真实布线。

## 结论先行

**全仓只有 2 处签名①布线 bug**：skill-evolution worker（#656 已修）+ memory_consolidator
（#661 本轮已修，同款 stale-gate）。其余 14 worker 无签名①②③④布线 bug。

剩余风险全是**签名⑤（live-data 依赖 + 单测 seeded）**或纯测试覆盖缺口——**布线是接通的，
只是单测没走 live 路径，CI 绿不代表真跑过**。按是否需起真栈验证排级：

## 16 worker 判级表

| worker | 判级 | 依据（file:line） |
|---|---|---|
| **memory_consolidator** | **🔴 需 live-smoke（本轮已修待验）** | #661 移 stale-gate；aux 现走金库。需起栈产生 transient 记忆簇验真 consolidation/purge 触发 |
| **reaper**（quota） | **🟠 需 live-smoke** | 布线 OK（app.py:673 真依赖）但 `enable_reaper` 硬编码 True 非 settings flag；`test_quota_in_memory.py` 零覆盖 `reaper.run_once()` 生命周期；需 live stale-reservation 数据 |
| **scheduler** | **🟠 需 live-smoke** | 布线完整（app.py:691/698 真依赖）但单测只用 `stub_agent_runtime`；真 manifest→agent 构造路径未覆盖 |
| **skill_curator** | **🟡 布线接通，live-smoke 选做** | 复核降级：`bump_last_used_at` 链真接通（app.py:718 真 recorder→:986/1022→agent_factory:798 `_record_skill_activity`），非 None；单测 seed `last_used_at` 是覆盖缺口非休眠 bug |
| skill_evolution_worker | ✅ 已 live-proven | 4.4 E2E（#655-658）真模型跑通 + auto-promote |
| curation_worker | ✅ 已 live-proven | 4.4 E2E 同链 |
| eval_worker | ✅ 已 live-proven | 11.4/11.5 E2E（trace 3/3 / adversarial 5/6） |
| trigger_firing | ✅ 已证可跑 | trajectory_recorder 时序正确（app.py:877 初始化在 scheduler.start 前），#657 已修 |
| webhook_delivery_worker | ✅ 已证可跑 | app.py:1201-1213 全真依赖；settings gate；HX-9（#595-599） |
| feedback_consumer | ✅ 已证可跑 | app.py:1191-1194 全真依赖；`mark_processed`（:193）幂等；独立写入 API |
| dlq_worker | ✅ 已证可跑 | app.py:1064-1067 真依赖；动态 embedder（非 env-dict 闸）；`ScriptedEmbedder` 全覆盖 |
| knowledge/ingestion | ✅ 已证可跑 | app.py:1045-1047 真依赖；独立 API 写入；动态 embedder |
| skill_rollback_monitor | ✅ 静态充分 | `enable_skill_rollback_monitor` 默认 OFF（settings.py:374）；启用时依赖全真；seeded 测覆盖回归判定完整 |
| mcp_probe | ✅ 静态充分 | 探活类，无 live-data 依赖 |
| approval_metrics | ✅ 静态充分 | 指标聚合，无副作用休眠风险 |
| runs.py（in-proc dispatch） | ✅ 已修 | #657 传真 trajectory_recorder |

## live-smoke 执行批次（建议）

起真栈一次（`make dev-up` + 金库粘 key + tenant_config），一轮 driver 覆盖 3 个 🔴🟠 项，
复用 4.4 E2E 同套起栈链：

1. **memory_consolidator（🔴 首要）**：建 per-user agent → 多轮对话写入 ≥3 条相似 transient
   长期记忆 → 手动触发/短 interval 跑 consolidator → 验 `MEMORY_CONSOLIDATED` 审计 + consolidated
   行出现。再塞 1 条 credential-shaped/time-bound 噪声 → 验 purge。**直接验证 #661 修复真生效**。
2. **reaper（🟠）**：起 quota reserve → 不 commit 放置过期 → 跑 reaper → 验 RESERVED→EXPIRED +
   ledger refund。
3. **scheduler（🟠）**：注册真 manifest 的 cron/interval trigger → 等触发 → 验 agent run 真起 +
   `last_fired_at` 更新 + `TRIGGER_FIRE` 审计（顺带覆盖真 agent 构造路径）。

skill_curator（🟡）选做：若上面 agent 绑了 skill，顺带验 `last_used_at` 被 bump + curator
active→stale 转移。

## 诚实交代

- 本审计是**静态布线核 + E2E 历史**，非全部 live 实测。🔴🟠 项的「需 live-smoke」是
  **不确定性标注**，不是已证休眠——区别于 #656/#657 那种已确诊 None/stale-gate。
- skill_curator 初判「需 live-smoke」，我亲自 grep `bump_last_used_at` 调用链后**降级**为
  「布线接通、选做」——印证 Explore agent 也会偏保守，须人核。
- 余下 11 worker 判「已证可跑/静态充分」依据是 app.py 接线传真依赖 + 单测无 seeded 绕过关键布线；
  其中 webhook/feedback/dlq/ingestion 的输入有独立写入 API（不依赖 live agent run），风险最低。
