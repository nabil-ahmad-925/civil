"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { FigureLink } from "@/components/standards/FigureViewer";
import { Card, ManualTag, PageHeader, RefStatusBadge } from "@/components/standards/ui";
import { listCrossRefs } from "@/lib/nodes/api";
import type { CrossRefRow } from "@/lib/nodes/types";
import type { CrossRefStatus } from "@/lib/standards/types";

type Filter = "all" | CrossRefStatus;

const filters: { id: Filter; label: string }[] = [
  { id: "all", label: "All" },
  { id: "resolved", label: "Resolved" },
  { id: "unresolved", label: "Unresolved" },
  { id: "awaiting_cross", label: "Awaiting cross-manual" },
  { id: "external", label: "Other document" },
];

export default function CrossReferencesPage() {
  const [refs, setRefs] = useState<CrossRefRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<Filter>("all");
  const [query, setQuery] = useState("");

  useEffect(() => {
    listCrossRefs()
      .then(setRefs)
      .catch((e) => setError(e instanceof Error ? e.message : "Could not load cross-references."));
  }, []);

  const counts = useMemo(() => {
    const c: Record<string, number> = { all: refs?.length ?? 0 };
    for (const r of refs ?? []) c[r.status] = (c[r.status] ?? 0) + 1;
    return c;
  }, [refs]);

  const rows = useMemo(() => {
    const q = query.trim().toLowerCase();
    return (refs ?? []).filter(
      (r) =>
        (filter === "all" || r.status === filter) &&
        (!q || `${r.mention} ${r.targetLabel} ${r.source.section} ${r.target?.section ?? ""}`.toLowerCase().includes(q)),
    );
  }, [refs, filter, query]);

  return (
    <div className="mx-auto max-w-6xl px-8 py-10">
      <PageHeader
        title="Cross-References"
        subtitle="Captured as unresolved while chunking, then linked by each manual's internal pass. References into another manual wait for the final cross-manual pass."
      />

      <div className="mb-6 grid gap-4 md:grid-cols-3">
        <Card>
          <p className="text-sm font-semibold text-principal-deep">1 · Capture</p>
          <p className="mt-1 text-xs text-muted">Every mention saved verbatim with its target label, unresolved.</p>
        </Card>
        <Card>
          <p className="text-sm font-semibold text-principal-deep">2 · Internal pass per manual</p>
          <p className="mt-1 text-xs text-muted">Same-manual targets matched to nodes once that manual is fully chunked.</p>
        </Card>
        <Card className="border-amber-200">
          <p className="text-sm font-semibold text-principal-deep">3 · Cross-manual pass</p>
          <p className="mt-1 text-xs text-amber-800">
            {counts.awaiting_cross ?? 0} reference{counts.awaiting_cross === 1 ? "" : "s"} waiting for another manual to be ingested.
          </p>
        </Card>
      </div>

      {error ? (
        <Card className="border-red-200">
          <p className="text-sm text-red-700">{error}</p>
        </Card>
      ) : !refs ? (
        <p className="text-sm text-muted">Loading cross-references…</p>
      ) : (
        <>
          <div className="mb-4 flex flex-wrap items-center gap-3">
            <div className="flex flex-wrap gap-1.5">
              {filters.map((f) => (
                <button
                  key={f.id}
                  onClick={() => setFilter(f.id)}
                  className={`rounded-full px-3 py-1 text-xs font-medium transition ${
                    f.id === filter ? "bg-principal-deep text-lime" : "bg-mint text-principal-deep hover:bg-lime"
                  }`}
                >
                  {f.label} ({counts[f.id] ?? 0})
                </button>
              ))}
            </div>
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search mention or section…"
              className="ml-auto w-64 rounded-lg border border-line bg-white px-3 py-1.5 text-sm outline-none focus:border-principal"
            />
          </div>

          <Card className="overflow-x-auto p-0">
            <table className="w-full text-left text-sm">
              <thead className="border-b border-line bg-mint/50 text-xs uppercase tracking-wider text-muted">
                <tr>
                  <th className="px-5 py-3 font-semibold">From</th>
                  <th className="px-5 py-3 font-semibold">Mention (verbatim)</th>
                  <th className="px-5 py-3 font-semibold">Target</th>
                  <th className="px-5 py-3 font-semibold">Status</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {rows.map((r) => (
                  <tr key={r.id} className="transition hover:bg-fondo">
                    <td className="px-5 py-3">
                      <Link
                        href={`/knowledge-base/browse?manual=${encodeURIComponent(r.source.manual)}&node=${r.source.id}`}
                        className="inline-flex items-center gap-2 hover:underline"
                        title={r.source.title}
                      >
                        <ManualTag id={r.source.manual} />
                        <span className="font-mono text-xs text-principal">{r.source.section}</span>
                      </Link>
                    </td>
                    <td className="px-5 py-3 text-texto">“{r.mention}”</td>
                    <td className="px-5 py-3">
                      <div className="flex items-center gap-2">
                        <ManualTag id={r.targetManual} />
                        {r.target ? (
                          <Link
                            href={`/knowledge-base/browse?manual=${encodeURIComponent(r.target.manual)}&node=${r.target.id}`}
                            className="text-principal hover:underline"
                          >
                            {r.target.section} {r.target.title}
                          </Link>
                        ) : r.targetFigure ? (
                          <FigureLink figure={r.targetFigure} />
                        ) : (
                          <span className="text-muted">{r.targetLabel}</span>
                        )}
                      </div>
                      {r.targetManual !== r.source.manual && r.targetManual !== "OTHER" && (
                        <p className="mt-1 text-[11px] text-muted">cross-manual</p>
                      )}
                    </td>
                    <td className="px-5 py-3">
                      <RefStatusBadge status={r.status} />
                      {r.resolvedBy && <p className="mt-1 text-[11px] text-muted">{r.resolvedBy.replace(/_/g, " ")}</p>}
                    </td>
                  </tr>
                ))}
                {rows.length === 0 && (
                  <tr>
                    <td colSpan={4} className="px-5 py-10 text-center text-sm text-muted">
                      {refs.length === 0 ? "No cross-references yet. Upload and ingest a manual first." : "No cross-references match."}
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </Card>
        </>
      )}
    </div>
  );
}
