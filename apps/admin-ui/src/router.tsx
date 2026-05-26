import { Navigate, Route, Routes } from "react-router-dom";
import { AgentsList } from "./pages/AgentsList";
import { AgentDetail } from "./pages/AgentDetail";
import { Curation } from "./pages/Curation";
import { MemoryAdmin } from "./pages/MemoryAdmin";
import { RunDetail } from "./pages/RunDetail";
import { RunsList } from "./pages/RunsList";
import { SettingsApiKeys } from "./pages/SettingsApiKeys";
import { SettingsAudit } from "./pages/SettingsAudit";
import { SkillDetail } from "./pages/SkillDetail";
import { SkillsList } from "./pages/SkillsList";
import { ComingSoon } from "./pages/ComingSoon";

export function AppRouter() {
  return (
    <Routes>
      <Route path="/" element={<Navigate to="/agents" replace />} />
      <Route path="/agents" element={<AgentsList />} />
      <Route path="/agents/:name/:version" element={<AgentDetail />} />
      <Route path="/agents/:name/:version/:tab" element={<AgentDetail />} />
      <Route path="/runs" element={<RunsList />} />
      <Route path="/runs/:threadId/:runId" element={<RunDetail />} />
      <Route path="/curation" element={<Curation />} />
      <Route path="/memory" element={<MemoryAdmin />} />
      <Route path="/skills" element={<SkillsList />} />
      <Route path="/skills/:skillId" element={<SkillDetail />} />
      <Route path="/triggers" element={<ComingSoon title="Triggers" />} />
      <Route path="/settings/api-keys" element={<SettingsApiKeys />} />
      <Route path="/settings/audit" element={<SettingsAudit />} />
      <Route path="/settings/*" element={<ComingSoon title="Settings" />} />
      <Route path="*" element={<ComingSoon title="404" />} />
    </Routes>
  );
}
