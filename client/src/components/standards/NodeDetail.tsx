import type { NodeDetail, NodeRef } from "@/lib/nodes/types";
import { FigureLink } from "./FigureViewer";
import { ClassificationBadge, ManualTag, RefStatusBadge, RequirementClassBadge } from "./ui";

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="mt-6">
      <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted">{title}</h3>
      {children}
    </section>
  );
}

/** A reference to another node. Clicking it opens that node, even in another manual. */
function NodeLink({ node, onOpen }: { node: NodeRef; onOpen: (n: NodeRef) => void }) {
  return (
    <button onClick={() => onOpen(node)} className="text-left font-medium text-principal hover:underline">
      <span className="mr-1.5 inline-block align-middle">
        <ManualTag id={node.manual} />
      </span>
      {node.title}
    </button>
  );
}

const oneLine = (s: string) => s.replace(/\s+/g, " ");

export default function NodeDetailView({ detail: d, onOpen }: { detail: NodeDetail; onOpen: (n: NodeRef) => void }) {
  const pages = d.pageStart === d.pageEnd ? `p. ${d.pageStart}` : `pp. ${d.pageStart}–${d.pageEnd}`;
  const applicability = d.conditions.filter((c) => c.kind === "applicability");
  const exceptions = d.conditions.filter((c) => c.kind === "exception");
  const untaggedNote = d.classification ? "None recorded." : "Extracted during tagging.";

  return (
    <div>
      <div className="flex flex-wrap items-center gap-2">
        <ManualTag id={d.manual} />
        <span className="font-mono text-sm text-muted">§ {d.section}</span>
        <ClassificationBadge value={d.classification} />
        <RequirementClassBadge value={d.requirementClass ?? undefined} />
      </div>
      <h2 className="mt-3 text-2xl font-bold text-principal-deep">{d.title}</h2>
      <p className="mt-1 text-sm text-muted">
        {d.source.originalName} · {d.source.version} · {pages}
      </p>
      {d.parent && (
        <p className="mt-2 text-sm text-muted">
          Inside: <NodeLink node={d.parent} onOpen={onOpen} />
        </p>
      )}

      <Section title="Source text (verbatim)">
        {d.text ? (
          <pre className="whitespace-pre-wrap break-words rounded-xl border-l-4 border-principal bg-mint/60 px-4 py-3 font-sans text-[15px] leading-relaxed text-texto">
            {d.text}
          </pre>
        ) : (
          <p className="rounded-xl border border-dashed border-line px-4 py-3 text-sm text-muted">
            No text under this heading. The next heading follows it directly.
          </p>
        )}
        <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 font-mono text-[11px] text-muted">
          <span>node: {d.id}</span>
          <span>source hash: {d.source.sha256.slice(0, 12)}</span>
        </div>
      </Section>

      <Section title="Applicability conditions">
        {applicability.length === 0 ? (
          <p className="text-sm text-muted">{untaggedNote}</p>
        ) : (
          <ul className="space-y-2">
            {applicability.map((c) => (
              <li key={c.id} className="rounded-lg border border-line px-3 py-2 text-sm">
                {c.input && (
                  <span className="mr-2 rounded bg-mint px-1.5 py-0.5 font-mono text-[11px] text-principal-deep">{c.input}</span>
                )}
                {c.text}
              </li>
            ))}
          </ul>
        )}
      </Section>

      <Section title="Exceptions">
        {exceptions.length === 0 ? (
          <p className="text-sm text-muted">{untaggedNote}</p>
        ) : (
          <ul className="space-y-2">
            {exceptions.map((c) => (
              <li key={c.id} className="rounded-lg border border-line px-3 py-2 text-sm">
                <p>{c.text}</p>
                {c.requiredInputs.length > 0 && (
                  <p className="mt-1.5 text-xs text-muted">
                    Needs:{" "}
                    {c.requiredInputs.map((i) => (
                      <span key={i} className="mr-1 rounded bg-lime px-1.5 py-0.5 font-mono text-[11px] text-principal-deep">
                        {i}
                      </span>
                    ))}
                    — missing inputs route to NOT_CHECKED, never fail.
                  </p>
                )}
              </li>
            ))}
          </ul>
        )}
      </Section>

      <Section title={`Cross-references out (${d.refsOut.length})`}>
        {d.refsOut.length === 0 ? (
          <p className="text-sm text-muted">No references in this node.</p>
        ) : (
          <ul className="space-y-2">
            {d.refsOut.map((r) => (
              <li key={r.id} className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-line px-3 py-2 text-sm">
                <span className="min-w-0">
                  “{oneLine(r.mention)}” →{" "}
                  {r.node ? (
                    <NodeLink node={r.node} onOpen={onOpen} />
                  ) : r.figure ? (
                    <FigureLink figure={r.figure} />
                  ) : (
                    <span className="font-medium text-muted">{r.targetLabel}</span>
                  )}
                </span>
                <RefStatusBadge status={r.status} />
              </li>
            ))}
          </ul>
        )}
      </Section>

      {d.refsIn.length > 0 && (
        <Section title={`Referenced by (${d.refsIn.length})`}>
          <ul className="space-y-1.5 text-sm">
            {d.refsIn.map((r) => (
              <li key={r.id}>
                <NodeLink node={r.node} onOpen={onOpen} />
                <span className="ml-2 text-xs text-muted">“{oneLine(r.mention)}”</span>
              </li>
            ))}
          </ul>
        </Section>
      )}

      {d.tags.length > 0 && (
        <Section title="Tags">
          <div className="flex flex-wrap gap-1.5">
            {d.tags.map((t) => (
              <span key={t} className="rounded-full bg-mint px-2.5 py-0.5 text-xs text-principal-deep">
                #{t}
              </span>
            ))}
          </div>
        </Section>
      )}
    </div>
  );
}
