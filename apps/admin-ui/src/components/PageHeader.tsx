import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { ChevronLeft } from "lucide-react";

interface PageHeaderProps {
  title: string;
  icon?: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
  /** Detail pages: a small "‹ label" link to the parent list (replaces the breadcrumb). */
  backTo?: { label: string; to: string };
}

export function PageHeader({ title, icon, subtitle, actions, backTo }: PageHeaderProps) {
  return (
    <div className="ew-page-header" data-testid="page-header">
      {backTo && (
        <Link to={backTo.to} className="ew-page-header-back" data-testid="page-header-back">
          <ChevronLeft size={14} strokeWidth={1.75} />
          <span>{backTo.label}</span>
        </Link>
      )}
      <div className="ew-page-header-row">
        <div className="ew-page-header-title">
          {icon}
          <h1>{title}</h1>
        </div>
        {actions && <div className="ew-page-header-actions">{actions}</div>}
      </div>
      {subtitle && <p className="ew-page-header-subtitle">{subtitle}</p>}
    </div>
  );
}
