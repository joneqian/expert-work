import { Empty } from "antd";
import { Construction } from "lucide-react";
import { useTranslation } from "react-i18next";

import { PageHeader } from "../components/PageHeader";

export function ComingSoon({ title }: { title: string }) {
  const { t } = useTranslation();
  return (
    <div>
      <PageHeader title={title} />
      <Empty
        image={<Construction size={48} strokeWidth={1.5} style={{ color: "var(--ew-text-tertiary)", margin: "0 auto" }} />}
        description={
          <>
            <div style={{ fontSize: 14, color: "var(--ew-text-primary)", marginBottom: 4 }}>
              {t("coming_soon.title_prefix")}
            </div>
            <div style={{ fontSize: 13, color: "var(--ew-text-tertiary)" }}>
              {t("coming_soon.body")}<br />
              {t("coming_soon.other_pages_prefix")}(<strong>{title}</strong>){t("coming_soon.other_pages_suffix")}
            </div>
          </>
        }
        style={{ padding: "80px 24px" }}
      />
    </div>
  );
}
