import { Navigate, Route, Routes } from "react-router-dom";
import { AgentsList } from "./pages/AgentsList";
import { ApprovalsList } from "./pages/ApprovalsList";
import { AgentDetail } from "./pages/AgentDetail";
import { ArtifactsList } from "./pages/ArtifactsList";
import { Curation } from "./pages/Curation";
import { EvalRunDetail } from "./pages/EvalRunDetail";
import { EvalRunsList } from "./pages/EvalRunsList";
import { KnowledgeAdmin } from "./pages/KnowledgeAdmin";
import { MemoryAdmin } from "./pages/MemoryAdmin";
import { RunDetail } from "./pages/RunDetail";
import { RunsList } from "./pages/RunsList";
import { SettingsApiKeys } from "./pages/SettingsApiKeys";
import { SettingsAudit } from "./pages/SettingsAudit";
import { SettingsEgressAudit } from "./pages/SettingsEgressAudit";
import { SettingsMembers } from "./pages/SettingsMembers";
import { SettingsPlatformConfig } from "./pages/SettingsPlatformConfig";
import { SettingsPlatformUsers } from "./pages/SettingsPlatformUsers";
import { SettingsRoleBindings } from "./pages/SettingsRoleBindings";
import { SettingsServiceAccounts } from "./pages/SettingsServiceAccounts";
import { SettingsTenantConfig } from "./pages/SettingsTenantConfig";
import { SettingsTenantCredentials } from "./pages/SettingsTenantCredentials";
import { SettingsTenantQuotas } from "./pages/SettingsTenantQuotas";
import { SettingsTenants } from "./pages/SettingsTenants";
import { SettingsMcpServers } from "./pages/SettingsMcpServers";
import { SettingsMcpCatalog } from "./pages/SettingsMcpCatalog";
import { SettingsPlatformSkills } from "./pages/SettingsPlatformSkills";
import { SettingsPlatformSkillDetail } from "./pages/SettingsPlatformSkillDetail";
import { SettingsRateCard } from "./pages/SettingsRateCard";
import { SettingsUsage } from "./pages/SettingsUsage";
import { SettingsBillingChargeback } from "./pages/SettingsBillingChargeback";
import { SkillDetail } from "./pages/SkillDetail";
import { SkillMarketplace } from "./pages/SkillMarketplace";
import { SkillsList } from "./pages/SkillsList";
import { TriggersList } from "./pages/TriggersList";
import { WebhooksList } from "./pages/WebhooksList";
import { ComingSoon } from "./pages/ComingSoon";

export function AppRouter() {
  return (
    <Routes>
      <Route path="/" element={<Navigate to="/agents" replace />} />
      <Route path="/agents" element={<AgentsList />} />
      <Route path="/agents/:name/:version" element={<AgentDetail />} />
      <Route path="/agents/:name/:version/:tab" element={<AgentDetail />} />
      <Route path="/runs" element={<RunsList />} />
      <Route path="/approvals" element={<ApprovalsList />} />
      <Route path="/runs/:threadId/:runId" element={<RunDetail />} />
      <Route path="/curation" element={<Curation />} />
      <Route path="/eval-runs" element={<EvalRunsList />} />
      <Route path="/eval-runs/:runId" element={<EvalRunDetail />} />
      <Route path="/memory" element={<MemoryAdmin />} />
      <Route path="/artifacts" element={<ArtifactsList />} />
      <Route path="/knowledge" element={<KnowledgeAdmin />} />
      <Route path="/skills" element={<SkillsList />} />
      <Route path="/skills/:skillId" element={<SkillDetail />} />
      <Route path="/skill-marketplace" element={<SkillMarketplace />} />
      <Route path="/triggers" element={<TriggersList />} />
      <Route path="/webhooks" element={<WebhooksList />} />
      <Route path="/settings/api-keys" element={<SettingsApiKeys />} />
      <Route path="/settings/service-accounts" element={<SettingsServiceAccounts />} />
      <Route path="/settings/role-bindings" element={<SettingsRoleBindings />} />
      <Route path="/settings/members" element={<SettingsMembers />} />
      <Route path="/settings/tenant-quotas" element={<SettingsTenantQuotas />} />
      <Route path="/settings/tenant-config" element={<SettingsTenantConfig />} />
      <Route path="/settings/tenants" element={<SettingsTenants />} />
      <Route path="/settings/credentials" element={<SettingsTenantCredentials />} />
      <Route path="/settings/platform" element={<SettingsPlatformConfig />} />
      <Route path="/settings/platform-users" element={<SettingsPlatformUsers />} />
      <Route path="/settings/mcp-catalog" element={<SettingsMcpCatalog />} />
      <Route path="/settings/platform-skills" element={<SettingsPlatformSkills />} />
      <Route
        path="/settings/platform-skills/:skillId"
        element={<SettingsPlatformSkillDetail />}
      />
      <Route path="/settings/audit" element={<SettingsAudit />} />
      <Route path="/settings/egress-audit" element={<SettingsEgressAudit />} />
      <Route path="/settings/mcp-servers" element={<SettingsMcpServers />} />
      <Route path="/settings/usage" element={<SettingsUsage />} />
      <Route path="/settings/billing-chargeback" element={<SettingsBillingChargeback />} />
      <Route path="/settings/rate-card" element={<SettingsRateCard />} />
      <Route path="/settings/*" element={<ComingSoon title="Settings" />} />
      <Route path="*" element={<ComingSoon title="404" />} />
    </Routes>
  );
}
