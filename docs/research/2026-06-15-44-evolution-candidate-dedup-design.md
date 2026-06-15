# 4.4 #5 — skill-evolution candidate 去重（不再无限重蒸）

> 4.4 真模型 E2E 暴露的第 5 个 gap（前 4 个已修/已配）。

## 问题

SkillEvolutionWorker.run_once 扫 `list_for_review_all_tenants(status=PENDING)`，处理后**从不标记 candidate
已演化** → 同一批 candidate 每个 interval（dev 15s）被反复处理 → **反复调 aux LLM 蒸馏 = 成本失控**，
且 happy-path 下会每 cycle 给同一 trajectory 生成一个新 DRAFT skill 版本。

E2E 实测：3 个 candidate 被 `processed:3` 每 15s 重复刷，永不停。单测只调 run_once 一次没暴露，live loop 才炸。

CurationCandidateStore 无「标记已演化」方法、CurationCandidateRecord 无演化追踪字段
（`status` 是 pending/promoted/dismissed = J.12 人审数据集流程，与 SE-6 演化正交，不能复用）。

## 方案

加 SE-6 专用标记 `evolved_at`，与 J.12 `status` 正交（一个 candidate 可「已被 SE-6 演化」+「仍 pending 人审」）：

1. `CurationCandidateRecord`：加 `evolved_at: datetime | None = None`。
2. Migration 0078：`curation_candidate` 加 `evolved_at TIMESTAMPTZ NULL`。
3. `CurationCandidateStore`：
   - `list_for_review_all_tenants(..., unevolved_only: bool = False)`：True 时过滤 `evolved_at IS NULL`。
     worker 传 True；admin 跨租户 review API（curation.py:181）保持默认 False（人审仍看全部）。
   - `mark_evolved(*, candidate_id, tenant_id, at)`：置 evolved_at。
4. worker：扫 `unevolved_only=True`；每个 candidate 处理完（成功**或** per-candidate 失败）`mark_evolved`
   → 同一 candidate 不再被重复蒸。失败的也标记（避免反复重试同一坏租户；后续重试策略另议）。

## 不做

- 不复用/改动 `status`（J.12 人审语义）。
- 不做演化重试策略（no_draft 的 candidate 标记后不再试；更好 aux 模型后重试是 follow-up）。
- 不动 SE-9 基准 / 演化核心逻辑。
