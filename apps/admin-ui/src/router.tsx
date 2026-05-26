import { Navigate, Route, Routes } from "react-router-dom";
import { AgentsList } from "./pages/AgentsList";
import { AgentDetail } from "./pages/AgentDetail";
import { RunDetail } from "./pages/RunDetail";
import { RunsList } from "./pages/RunsList";
import { SettingsApiKeys } from "./pages/SettingsApiKeys";
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
      <Route path="/curation" element={<ComingSoon title="Curation+Eval" />} />
      <Route path="/memory" element={<ComingSoon title="Memory" />} />
      <Route path="/skills" element={<ComingSoon title="Skills" />} />
      <Route path="/triggers" element={<ComingSoon title="Triggers" />} />
      <Route path="/settings/api-keys" element={<SettingsApiKeys />} />
      <Route path="/settings/*" element={<ComingSoon title="Settings" />} />
      <Route path="*" element={<ComingSoon title="404" />} />
    </Routes>
  );
}
