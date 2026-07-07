# Stream U PR F — Set Member Password from Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`).

**Goal:** A tenant admin (or system_admin switched into the tenant) can set a member's password directly from the Expert Work admin UI — no Keycloak console detour. Admin types a temporary password (forced change on first login). This unblocks dev onboarding where no SMTP exists.

**Architecture:** Add `reset_password(*, user_id, password, temporary)` to the Keycloak admin client (`PUT /users/{id}/reset-password`). New `POST /v1/members/{member_id}/reset-password` (`require("user","write")`, body `{password: SecretStr}`, backend forces `temporary=True`, audits `member:password_reset` with NO value). Frontend: a "Set password" action on the members page opening a modal with a masked password input.

**Tech Stack:** FastAPI + pydantic SecretStr, httpx; React/Antd/Vitest. **Security:** password is `SecretStr` end-to-end, never logged, audit records actor/member only (no value), input `type=password`, TLS-only admin endpoint.

---

## Task 1: Keycloak `reset_password` + endpoint + audit

**Files:**
- `services/control-plane/src/control_plane/keycloak/admin_client.py` (Protocol + Http impl)
- `services/control-plane/src/control_plane/api/members.py` (endpoint)
- `packages/expert-work-protocol/src/expert_work/protocol/audit.py` (1 enum value)
- Tests: `services/control-plane/tests/test_members_api.py` (or wherever members API is tested) + the fake Keycloak client used in tests (`services/control-plane/tests/test_tenants_first_admin.py` defines one — find the shared fake; if it's a local class, the members test likely has its own fake — add `reset_password` to whichever the members API test uses)

- [ ] **Step 1: AuditAction**

In `protocol/audit.py` `AuditAction`, after `MEMBER_ACTIVATE = "member:activate"`:
```python
    MEMBER_PASSWORD_RESET = "member:password_reset"
```

- [ ] **Step 2: Keycloak client `reset_password`**

In `keycloak/admin_client.py`:
- Protocol `KeycloakAdminClient` (after `set_enabled`):
```python
    async def reset_password(self, *, user_id: str, password: str, temporary: bool) -> None:
        """Set the user's password. ``temporary=True`` forces a change on next login."""
        ...
```
- `HttpKeycloakAdminClient.reset_password` (mirror `set_enabled`'s error handling):
```python
    async def reset_password(self, *, user_id: str, password: str, temporary: bool) -> None:
        try:
            resp = await self._http.put(
                f"{self._admin}/users/{user_id}/reset-password",
                json={"type": "password", "value": password, "temporary": temporary},
                headers=await self._auth_headers(),
            )
        except httpx.HTTPError as exc:
            raise KeycloakUnavailableError(f"reset_password request failed: {exc}") from exc
        if resp.status_code >= 500:
            raise KeycloakUnavailableError(f"reset_password 5xx: HTTP {resp.status_code}")
        if resp.status_code not in (200, 204):
            raise KeycloakUnavailableError(
                f"reset_password unexpected status: HTTP {resp.status_code}"
            )
```
> Do NOT log `password`. If `KeycloakAdminClient` is a `Protocol`, any concrete fake in tests must also gain `reset_password` or it won't satisfy the type — but Python Protocols are structural; add it to the test fake regardless so the call is recorded/asserted.

- [ ] **Step 3: Endpoint**

In `api/members.py`:
- Add a body model near the other `BaseModel`s:
```python
class ResetPasswordBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    password: SecretStr = Field(min_length=8, max_length=256)
```
(import `SecretStr` from pydantic — add to the existing `from pydantic import ...` line.)
- Add the handler in `build_members_router()` (or wherever routes are defined; mirror `resend`):
```python
    @router.post("/{member_id}/reset-password")
    async def reset_password(
        member_id: UUID,
        body: ResetPasswordBody,
        principal: Annotated[Principal, Depends(require("user", "write"))],
        member_repo: Annotated[TenantMemberStore, Depends(_get_member_repo)],
        keycloak: Annotated[KeycloakAdminClient, Depends(_get_keycloak)],
        audit: Annotated[AuditLogger, Depends(_get_audit)],
    ) -> dict[str, object]:
        member = await member_repo.get(tenant_id=principal.tenant_id, member_id=member_id)
        if member is None:
            raise HTTPException(status_code=404, detail={"code": "MEMBER_NOT_FOUND"})
        if member.keycloak_user_id is None:
            raise HTTPException(
                status_code=409,
                detail={"code": "MEMBER_NO_KEYCLOAK_USER", "message": "member has no keycloak account yet"},
            )
        try:
            await keycloak.reset_password(
                user_id=member.keycloak_user_id,
                password=body.password.get_secret_value(),
                temporary=True,
            )
        except KeycloakUnavailableError as exc:
            raise HTTPException(
                status_code=502,
                detail={"code": "KEYCLOAK_UNAVAILABLE", "message": "keycloak unreachable; retry"},
            ) from exc
        await emit(
            audit,
            tenant_id=principal.tenant_id,
            actor_id=principal.subject_id,
            action=AuditAction.MEMBER_PASSWORD_RESET,
            resource_type="user",
            resource_id=str(member_id),
            trace_id=current_trace_id_hex(),
        )
        return {"success": True, "data": {"member_id": str(member_id)}, "error": None}
```
> Match the exact `emit(...)` kwargs the other members handlers use. `KeycloakAdminClient`, `KeycloakUnavailableError`, `require`, `_get_member_repo`, `_get_keycloak`, `_get_audit`, `current_trace_id_hex` are already imported.

- [ ] **Step 4: Tests (RED→GREEN)**

READ the members API test module + its fake Keycloak. Add `reset_password` to the fake (record `(user_id, password, temporary)` calls; optionally a flag to raise `KeycloakUnavailableError`). Cases:
- happy path: an active member with a keycloak_user_id → `POST /v1/members/{id}/reset-password {"password":"hunter2pass"}` → 200; the fake recorded `temporary=True` and the right user_id + password.
- member without keycloak_user_id → 409 `MEMBER_NO_KEYCLOAK_USER`.
- unknown member → 404 `MEMBER_NOT_FOUND`.
- non-authorized principal (no `user:write`) → 403 (mirror how other members tests assert authz).
- keycloak unavailable → 502 `KEYCLOAK_UNAVAILABLE`.
- (security) password too short (<8) → 422.
Run: `cd services/control-plane && uv run python -m pytest tests/test_members_api.py -k password -v` then the full members test file.

- [ ] **Step 5: pre-commit + commit**

```bash
uv run pre-commit run --files services/control-plane/src/control_plane/keycloak/admin_client.py services/control-plane/src/control_plane/api/members.py packages/expert-work-protocol/src/expert_work/protocol/audit.py services/control-plane/tests/test_members_api.py
git add services/control-plane packages/expert-work-protocol/src/expert_work/protocol/audit.py
git commit -m "feat(stream-u): PR F — keycloak reset_password + POST /v1/members/{id}/reset-password"
```

---

## Task 2: Frontend — Set password action

**Files:**
- `apps/admin-ui/src/api/members.ts` (SDK)
- `apps/admin-ui/src/pages/SettingsMembers.tsx` (action + modal)
- `apps/admin-ui/src/i18n/locales/{en,zh-CN}.ts`
- Test: `apps/admin-ui/src/pages/__tests__/SettingsMembers.test.tsx` (extend if exists; else create)

- [ ] **Step 1: SDK**

In `members.ts`:
```ts
export async function resetMemberPassword(memberId: string, password: string): Promise<void> {
  await postJson(`/v1/members/${memberId}/reset-password`, { password });
}
```
(`postJson` already imported.)

- [ ] **Step 2: i18n**

Add a `members` (or the existing members namespace — READ which namespace SettingsMembers uses) keys in BOTH locales:
- set_password "Set password"/"设置密码"
- set_password_title "Set a temporary password"/"设置临时密码"
- set_password_hint "The member must change it on first login."/"成员首次登录时必须修改。"
- set_password_label "Temporary password"/"临时密码"
- set_password_placeholder "At least 8 characters"/"至少 8 位"
- set_password_submit "Set password"/"设置"
- set_password_ok "Password set."/"密码已设置。"
- set_password_failed "Failed to set password"/"设置密码失败"
- set_password_too_short "At least 8 characters"/"至少 8 位"
(Put them under whatever interface block the members page already uses; typecheck enforces zh parity. If members strings live under a `settings_members`/`members_page` block, add there.)

- [ ] **Step 3: Page action (tests first)**

READ `SettingsMembers.tsx` for its action-column pattern (Resend/Remove buttons gated by status) + how it calls SDK + shows `App.useApp()` messages, and its test file.
Test (RED): mock `resetMemberPassword`; render members with one active member having a `keycloak_user_id`; click `members-set-password-<id>` → a modal opens; type a password; submit → `resetMemberPassword` called with (id, password) + success message. Also: submitting <8 chars shows the inline error and does NOT call the SDK.
Implement: add a "Set password" button (`data-testid={`members-set-password-${m.id}`}`) in the actions column for members where `m.keycloak_user_id` is set and status is `active` or `invited`. Clicking opens an Antd `Modal` with `Input.Password` (`data-testid="members-set-password-input"`) + the hint; the modal's OK (`data-testid="members-set-password-submit"`) validates length ≥8 then calls `resetMemberPassword(m.id, pw)`, on success `message.success` + close, on error `message.error`. Keep the password only in local modal state; clear on close.

- [ ] **Step 4: Verify**

Run: `cd apps/admin-ui && pnpm run typecheck && pnpm vitest run src/pages/__tests__/SettingsMembers.test.tsx && pnpm run build`. Stale LSP diagnostics: typecheck exit 0 authoritative.

- [ ] **Step 5: pre-commit + commit**

```bash
uv run pre-commit run --files apps/admin-ui/src/api/members.ts apps/admin-ui/src/pages/SettingsMembers.tsx apps/admin-ui/src/pages/__tests__/SettingsMembers.test.tsx apps/admin-ui/src/i18n/locales/en.ts apps/admin-ui/src/i18n/locales/zh-CN.ts
git add apps/admin-ui/src/api/members.ts apps/admin-ui/src/pages/SettingsMembers.tsx apps/admin-ui/src/pages/__tests__/SettingsMembers.test.tsx apps/admin-ui/src/i18n/locales/en.ts apps/admin-ui/src/i18n/locales/zh-CN.ts
git commit -m "feat(stream-u): PR F — set member password action (admin temporary password)"
```

---

## Task 3: backlog tick + whole-PR gate

- [ ] Tick `U-F` in `docs/ITERATION-PLAN.md` (`[ ]`→`[x]`, append `(PR F)`); commit the U-F plan doc.
- [ ] Whole-PR preflight: `uv run pre-commit run --all-files`; control-plane `pytest -m "not integration"`; admin-ui typecheck+vitest+build. Fix drift.
- [ ] Open PR `stream-u/f-set-password`.

## Self-Review (controller)
- **Security:** `password: SecretStr`, `get_secret_value()` only at the keycloak call; never logged; audit has no value; input `type=password` (Input.Password); endpoint `require("user","write")`. ✅
- **temporary=True forced server-side** (admin can't set a permanent password by accident; member must rotate). ✅
- **AuditAction single-location** (+ reuse `resource_type="user"`). ✅
- **No-keycloak-user guard** (409) so we don't call KC with a null id. ✅
