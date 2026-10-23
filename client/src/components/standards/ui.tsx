import type { Classification, CrossRefStatus, RequirementClass, StageStatus } from "@/lib/standards/types";

export function PageHeader({
  title,
  subtitle,
  sampleData = true,
  children,
}: {
  title: string;
  subtitle?: string;
  /** Shows the "Sample data" badge; turn off for pages backed by real data. */
  sampleData?: boolean;
  children?: React.ReactNode;
}) {
  return (
    <header className="mb-8 flex flex-wrap items-end justify-between gap-4">
      <div>
        <div className="mb-2 flex items-center gap-2">
          <span className="text-xs font-semibold uppercase tracking-wider text-principal">Standards · Phase 1</span>
          {sampleData && (
            <span className="rounded-full border border-line bg-blanco px-2 py-0.5 text-[11px] text-muted">Sample data</span>
          )}
        </div>
        <h1 className="text-3xl font-bold text-principal-deep">{title}</h1>
        {subtitle && <p className="mt-2 max-w-2xl text-muted">{subtitle}</p>}
      </div>
      {children}
    </header>
  );
}

export function Card({ className = "", children }: { className?: string; children: React.ReactNode }) {
  return <div className={`rounded-2xl border border-line bg-blanco p-5 shadow-sm ${className}`}>{children}</div>;
}

const stageStyles: Record<StageStatus, { label: string; cls: string; dot: string }> = {
  done: { label: "Done", cls: "bg-mint text-principal-deep", dot: "bg-principal" },
  running: { label: "Running", cls: "bg-lime text-principal-deep", dot: "bg-principal animate-pulse" },
  pending: { label: "Pending", cls: "bg-fondo text-muted border border-line", dot: "bg-muted/50" },
  blocked: { label: "Blocked", cls: "bg-amber-50 text-amber-800 border border-amber-200", dot: "bg-amber-500" },
};

export function StagePill({ status }: { status: StageStatus }) {
  const s = stageStyles[status];
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium ${s.cls}`}>
      <span className={`h-1.5 w-1.5 rounded-full ${s.dot}`} />
      {s.label}
    </span>
  );
}

export function Progress({ done, total }: { done: number; total: number }) {
  const pct = total ? Math.round((done / total) * 100) : 0;
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-mint">
      <div className="h-full rounded-full bg-principal transition-all" style={{ width: `${pct}%` }} />
    </div>
  );
}

const classStyles: Record<Classification, { label: string; cls: string }> = {
  requirement: { label: "Requirement", cls: "bg-principal text-blanco" },
  guidance: { label: "Guidance", cls: "bg-lime text-principal-deep" },
  non_governing: { label: "Non-governing", cls: "bg-mint text-muted border border-line" },
};

export function ClassificationBadge({ value }: { value: Classification | null }) {
  if (!value) {
    return <span className="rounded-full border border-dashed border-line px-2.5 py-0.5 text-xs text-muted">Not tagged yet</span>;
  }
  const s = classStyles[value];
  return <span className={`rounded-full px-2.5 py-0.5 text-xs font-medium ${s.cls}`}>{s.label}</span>;
}

const reqClassLabel: Record<RequirementClass, string> = {
  boldface: "Boldface standard",
  underlined: "Underlined standard",
  approval_required: "Approval required",
  permissive: "Permissive",
};

export function RequirementClassBadge({ value }: { value?: RequirementClass }) {
  if (!value) return null;
  return (
    <span className="rounded-full border border-principal/30 px-2.5 py-0.5 text-xs font-medium text-principal">
      {reqClassLabel[value]}
    </span>
  );
}

const refStyles: Record<CrossRefStatus, { label: string; cls: string }> = {
  resolved: { label: "Resolved", cls: "bg-mint text-principal-deep" },
  unresolved: { label: "Unresolved", cls: "bg-amber-50 text-amber-800 border border-amber-200" },
  awaiting_cross: { label: "Awaiting cross-manual pass", cls: "bg-lime text-principal-deep" },
  external: { label: "Other document", cls: "bg-slate-100 text-slate-700 border border-slate-200" },
};

export function RefStatusBadge({ status }: { status: CrossRefStatus }) {
  const s = refStyles[status];
  return <span className={`whitespace-nowrap rounded-full px-2.5 py-0.5 text-xs font-medium ${s.cls}`}>{s.label}</span>;
}

export function ManualTag({ id }: { id: string }) {
  return (
    <span className="rounded-md bg-principal-deep px-1.5 py-0.5 font-mono text-[11px] font-semibold text-lime">{id}</span>
  );
}
