/**
 * Curation+Eval page — Stream H.4 PR 1.
 *
 * Single ``/curation`` route hosting two sub-panels: candidates review
 * (default) + eval-datasets CRUD. Internal Antd ``<Tabs>`` switch — no
 * URL change, since the panels share the same tenant scope + cross-tenant
 * banner shell. Sub-panels live under ``pages/curation/`` to keep the
 * outer file small (Mini-ADR style file org).
 */
import { useState } from "react";
import { Breadcrumb, Tabs } from "antd";
import { ChevronRight, CheckSquare } from "lucide-react";
import { useTranslation } from "react-i18next";

import { CandidatesPanel } from "./curation/CandidatesPanel";
import { EvalDatasetsPanel } from "./curation/EvalDatasetsPanel";

type CurationTab = "candidates" | "datasets";

export function Curation() {
  const { t } = useTranslation();
  const [activeTab, setActiveTab] = useState<CurationTab>("candidates");

  return (
    <div data-testid="curation-root">
      <div className="hx-page-header">
        <Breadcrumb
          separator={<ChevronRight size={12} strokeWidth={1.5} />}
          items={[{ title: t("common.home") }, { title: t("curation.page_title") }]}
        />
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 12,
            marginTop: 8,
            marginBottom: 16,
          }}
        >
          <CheckSquare size={20} strokeWidth={1.5} />
          <h1 style={{ margin: 0 }}>{t("curation.page_title")}</h1>
        </div>
        <p
          style={{ color: "var(--hx-text-secondary)", fontSize: 13, margin: "0 0 12px" }}
        >
          {t("curation.subtitle")}
        </p>
      </div>

      <Tabs
        activeKey={activeTab}
        onChange={(k) => setActiveTab(k as CurationTab)}
        items={[
          { key: "candidates", label: t("curation.tab_candidates") },
          { key: "datasets", label: t("curation.tab_datasets") },
        ]}
        data-testid="curation-tabs"
      />

      {activeTab === "candidates" && <CandidatesPanel />}
      {activeTab === "datasets" && <EvalDatasetsPanel />}
    </div>
  );
}
