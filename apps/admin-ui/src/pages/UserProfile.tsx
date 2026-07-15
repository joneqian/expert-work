/**
 * User profile — the top-level, agent-agnostic user-detail page
 * (``/users/:userId``), keyed only on the surrogate ``userId``.
 *
 * Distinct from the agent-scoped ``UserDetail`` (``/agents/:name/:version/
 * users/:userId``): this is reached from the tenant-wide Users roster and
 * assembles the user's cross-agent assets — conversations, memory,
 * workspace and usage. Read-only observability, except memory edit/forget
 * and workspace file/artifact deletion. Admin-only (self-guards + backend
 * 403). The primary identifier shown is ``subject_id`` (the passed-in
 * ``user_id``), not the surrogate.
 */
import { useEffect, useState } from "react";
import { Alert, Empty, Space, Tabs, Tag, Typography } from "antd";
import { UserRound } from "lucide-react";
import { useLocation, useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";

import { getTenantUser } from "../api/users";
import { useAuth } from "../auth/AuthContext";
import { PageHeader } from "../components/PageHeader";
import { ConversationsPane } from "./user_profile/ConversationsPane";
import { MemoryPane } from "./user_profile/MemoryPane";
import { UsagePane } from "./user_profile/UsagePane";
import { WorkspacePane } from "./user_profile/WorkspacePane";

const { Text } = Typography;

interface UserProfileNavState {
  subjectId?: string;
  displayName?: string;
  isMember?: boolean;
  memberRole?: string;
}

export function UserProfile() {
  const { t } = useTranslation();
  const { userId } = useParams<{ userId: string }>();
  const location = useLocation();
  const { identity } = useAuth();

  const isAdmin =
    (identity?.isSystemAdmin ?? false) || (identity?.roles.includes("admin") ?? false);
  const denied = (identity?.serverResolved ?? false) && !isAdmin;

  const navState = (location.state as UserProfileNavState | null) ?? {};
  // Router state paints instantly; the registry fetch covers direct URL
  // opens / refreshes. Best-effort — a 404 keeps the surrogate fallback.
  const [fetchedSubjectId, setFetchedSubjectId] = useState<string | null>(null);
  const [fetchedName, setFetchedName] = useState<string | null>(null);
  useEffect(() => {
    if (!userId || denied) return;
    let cancelled = false;
    getTenantUser(userId)
      .then((u) => {
        if (cancelled) return;
        setFetchedSubjectId(u.subject_id);
        if (u.display_name) setFetchedName(u.display_name);
      })
      .catch(() => {
        // 404 / 403 — keep the fallbacks.
      });
    return () => {
      cancelled = true;
    };
  }, [userId, denied]);

  if (!userId) {
    return <Empty description="Missing route params" style={{ marginTop: 80 }} />;
  }

  const subjectId = navState.subjectId ?? fetchedSubjectId ?? undefined;
  const displayName = navState.displayName ?? fetchedName ?? undefined;
  const isMember = navState.isMember;
  const memberRole = navState.memberRole;

  if (denied) {
    return (
      <div data-testid="user-profile-root">
        <PageHeader
          icon={<UserRound size={18} strokeWidth={1.5} />}
          title={t("users_page.page_title")}
          backTo={{ label: t("user_profile.back_label"), to: "/users" }}
        />
        <Alert
          type="warning"
          showIcon
          message={t("users_page.not_admin_title")}
          description={t("users_page.not_admin_body")}
          data-testid="user-profile-not-admin"
        />
      </div>
    );
  }

  return (
    <div data-testid="user-profile-root">
      <PageHeader
        icon={<UserRound size={18} strokeWidth={1.5} />}
        title={displayName ?? subjectId ?? `${userId.slice(0, 12)}…`}
        backTo={{ label: t("user_profile.back_label"), to: "/users" }}
        subtitle={
          <Space size={8} wrap>
            {isMember !== undefined && (
              <Tag color={isMember ? "blue" : "orange"}>
                {isMember
                  ? memberRole
                    ? `${t("users_page.tag_member")} · ${memberRole}`
                    : t("users_page.tag_member")
                  : t("users_page.tag_external")}
              </Tag>
            )}
            {subjectId && (
              <span>
                <Text type="secondary" style={{ fontSize: 12, marginInlineEnd: 4 }}>
                  {t("user_profile.subject_id_label")}
                </Text>
                <Text code copyable style={{ fontSize: 12 }} data-testid="user-profile-subject-id">
                  {subjectId}
                </Text>
              </span>
            )}
            <span>
              <Text type="secondary" style={{ fontSize: 11, marginInlineEnd: 4 }}>
                {t("user_profile.surrogate_label")}
              </Text>
              <Text code copyable type="secondary" style={{ fontSize: 11 }}>
                {userId}
              </Text>
            </span>
          </Space>
        }
      />
      <Alert
        type="info"
        showIcon
        message={t("user_profile.banner")}
        style={{ marginBottom: 16 }}
        data-testid="user-profile-banner"
      />
      <Tabs
        defaultActiveKey="conversations"
        items={[
          {
            key: "conversations",
            label: t("user_profile.tab_conversations"),
            children: <ConversationsPane userId={userId} />,
          },
          {
            key: "memory",
            label: t("user_profile.tab_memory"),
            children: <MemoryPane userId={userId} />,
          },
          {
            key: "workspace",
            label: t("user_profile.tab_workspace"),
            children: <WorkspacePane userId={userId} />,
          },
          {
            key: "usage",
            label: t("user_profile.tab_usage"),
            children: <UsagePane userId={userId} />,
          },
        ]}
      />
    </div>
  );
}
