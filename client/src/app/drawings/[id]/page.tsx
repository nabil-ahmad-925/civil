"use client";

import Link from "next/link";
import { use, useEffect, useState } from "react";
import { Card, PageHeader } from "@/components/standards/ui";
import { bindingEvidenceUrl, factEvidenceUrl, getReview, sheetImageUrl } from "@/lib/drawings/api";
import type { Box, DrawingFact, DrawingFeature, DrawingNote, DrawingReview, SheetReview } from "@/lib/drawings/types";

const pretty = (word: string) => word.replace(/_/g, " ");
const featureName = (f: DrawingFeature) => pretty(f.type) + (f.label ? ` — ${f.label}` : "");

const CONFIRMATION: Record<DrawingFact["confirmation"], { label: string; cls: string }> = {
  confirmed: { label: "Second read agrees", cls: "bg-mint text-principal-deep" },
  disputed: { label: "Second read disagrees", cls: "bg-red-50 text-red-700 border border-red-200" },
  not_checked: { label: "Not confirmed", cls: "bg-amber-50 text-amber-800 border border-amber-200" },
};

function Overlay({ box, sheet, tone, selected, title, onClick }: {
  box: Box;
  sheet: SheetReview["sheet"];
  tone: "feature" | "note" | "flag";
  selected: boolean;
  title: string;
  onClick: () => void;
}) {
  const colors = {
    feature: "border-principal bg-principal/10",
    note: "border-sky-600",
    flag: "border-2 border-red-600",
  }[tone];
  return (
    <button
      type="button"
      title={title}
      aria-label={title}
      onClick={onClick}
      className={`absolute border ${colors} ${selected ? "z-10 ring-4 ring-lime" : ""}`}
      style={{
        left: `${(box[0] / sheet.width) * 100}%`,
        top: `${(box[1] / sheet.height) * 100}%`,
        width: `${((box[2] - box[0]) / sheet.width) * 100}%`,
        height: `${((box[3] - box[1]) / sheet.height) * 100}%`,
      }}
    />
  );
}

function FactRow({ fact }: { fact: DrawingFact }) {
  if (fact.status === "NOT_FOUND") {
    return (
      <li className="rounded-xl border border-line bg-fondo p-3 text-sm text-muted">
        <span className="font-medium">{pretty(fact.term)}</span>: not printed here
      </li>
    );
  }
  const c = CONFIRMATION[fact.confirmation];
  return (
    <li id={fact.id} className="rounded-xl border border-line p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm font-medium text-texto">
          {pretty(fact.term)}: {String(fact.value)} {fact.unit}
        </p>
        <span className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${c.cls}`}>{c.label}</span>
      </div>
      <p className="mt-1 font-mono text-xs text-muted">“{fact.verbatimText}”</p>
      <p className="mt-1 text-xs text-muted">{fact.confidence} confidence · proposed</p>
      {fact.hasEvidence && (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={factEvidenceUrl(fact.id)}
          alt={`The crop this value was read from: ${fact.verbatimText}`}
          loading="lazy"
          className="mt-2 max-h-52 max-w-full rounded-lg border border-line bg-blanco object-contain"
        />
      )}
    </li>
  );
}

function Sheet({ review }: { review: SheetReview }) {
  const { sheet, features, notes, bindings, facts, needsReview } = review;
  const [selected, setSelected] = useState<string | null>(null);
  const flagged = new Set(needsReview.map((i) => i.targetId));
  const featureOf = new Map(features.map((f) => [f.id, f]));
  const noteOf = new Map(notes.map((n) => [n.id, n]));
  const chosenFeature = selected ? featureOf.get(selected) : undefined;
  const chosenNote = selected ? noteOf.get(selected) : undefined;
  const sheetFacts = facts.filter((f) => f.featureId === null);

  function linksOfNote(n: DrawingNote) {
    return bindings.filter((b) => b.noteId === n.id);
  }

  return (
    <section className="mb-10">
      <h2 className="mb-3 text-lg font-semibold text-principal-deep">
        Sheet {sheet.number} <span className="text-sm font-normal text-muted">· {sheet.originalName}</span>
      </h2>

      <div className="grid gap-5 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        <Card className="self-start overflow-hidden p-2">
          <div className="relative">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={sheetImageUrl(sheet.id)} alt={`Sheet ${sheet.number}`} className="block w-full" />
            {features.map((f) =>
              f.bbox ? (
                <Overlay key={f.id} box={f.bbox} sheet={sheet} tone={flagged.has(f.id) ? "flag" : "feature"} selected={selected === f.id} title={featureName(f)} onClick={() => setSelected(f.id)} />
              ) : null,
            )}
            {notes.map((n) =>
              n.bbox ? (
                <Overlay key={n.id} box={n.bbox} sheet={sheet} tone={flagged.has(n.id) ? "flag" : "note"} selected={selected === n.id} title={n.text} onClick={() => setSelected(n.id)} />
              ) : null,
            )}
          </div>
          <p className="px-2 py-2 text-xs text-muted">Green: feature. Blue: note. Red: needs a look. Click one for its details.</p>
        </Card>

        <div className="min-w-0 space-y-5">
          <Card>
            <h3 className="font-semibold text-principal-deep">Needs a look ({needsReview.length})</h3>
            {needsReview.length === 0 && <p className="mt-2 text-sm text-muted">Nothing flagged on this sheet.</p>}
            <ul className="mt-2 space-y-2">
              {needsReview.map((item, i) => (
                <li key={i}>
                  <button
                    type="button"
                    disabled={!item.targetId}
                    onClick={() => {
                      if (!item.targetId) return;
                      if (featureOf.has(item.targetId) || noteOf.has(item.targetId)) setSelected(item.targetId);
                      else {
                        const fact = facts.find((f) => f.id === item.targetId);
                        if (fact?.featureId) setSelected(fact.featureId);
                      }
                    }}
                    className="w-full rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-left text-sm text-red-800 enabled:hover:bg-red-100"
                  >
                    {item.message}
                  </button>
                </li>
              ))}
            </ul>
          </Card>

          {chosenFeature && (
            <Card>
              <h3 className="font-semibold capitalize text-principal-deep">{featureName(chosenFeature)}</h3>
              <p className="mt-1 text-xs text-muted">
                {chosenFeature.confidence} confidence · found on the {chosenFeature.foundBy === "tile" ? "tile sweep" : "whole sheet"} · proposed
              </p>
              {chosenFeature.related.map((r) => {
                const other = featureOf.get(r.featureId);
                return other ? (
                  <p key={r.featureId} className="mt-1 text-xs text-muted">
                    {r.relation} {featureName(other)}
                  </p>
                ) : null;
              })}
              <ul className="mt-3 space-y-2">
                {facts.filter((f) => f.featureId === chosenFeature.id).map((f) => <FactRow key={f.id} fact={f} />)}
              </ul>
              {facts.every((f) => f.featureId !== chosenFeature.id) && (
                <p className="mt-3 text-sm text-red-700">Its values could not be read. See the list above.</p>
              )}
            </Card>
          )}

          {chosenNote && (
            <Card>
              <h3 className="font-semibold text-principal-deep">Note</h3>
              <p className="mt-2 font-mono text-sm text-texto">“{chosenNote.text}”</p>
              <p className="mt-1 text-xs text-muted">
                {chosenNote.scope === "unbound" ? "No clear owner" : chosenNote.scope === "sheet" ? "Applies to the whole sheet" : "Belongs to a feature"} · {chosenNote.confidence} confidence · proposed
              </p>
              {linksOfNote(chosenNote).map((b) => (
                <div key={b.id} className="mt-3 border-t border-line pt-3">
                  <p className="text-sm text-texto">
                    Linked to <span className="font-medium">{featureOf.get(b.featureId) ? featureName(featureOf.get(b.featureId)!) : "a feature"}</span> ({pretty(b.evidence)}, {b.confidence} confidence)
                  </p>
                  {b.hasEvidence && (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img src={bindingEvidenceUrl(b.id)} alt="The crop that shows this link" loading="lazy" className="mt-2 max-h-52 max-w-full rounded-lg border border-line object-contain" />
                  )}
                </div>
              ))}
            </Card>
          )}

          {sheetFacts.length > 0 && (
            <Card>
              <h3 className="font-semibold text-principal-deep">Applies to the whole sheet</h3>
              <ul className="mt-3 space-y-2">{sheetFacts.map((f) => <FactRow key={f.id} fact={f} />)}</ul>
            </Card>
          )}
        </div>
      </div>
    </section>
  );
}

export default function DrawingReviewPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [review, setReview] = useState<DrawingReview | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getReview(id)
      .then(setReview)
      .catch((e) => setError(e instanceof Error ? e.message : "Could not load the review."));
  }, [id]);

  const s = review?.drawing.summary;
  return (
    <div className="mx-auto max-w-7xl px-8 py-10">
      <Link href="/drawings" className="text-sm font-medium text-principal hover:underline">
        ← All drawings
      </Link>
      <div className="mt-3">
        <PageHeader
          title={review?.drawing.name ?? "Drawing"}
          subtitle="Everything here is what a model proposed. Nothing is verified until an engineer has checked it."
          sampleData={false}
          eyebrow="Drawings · Phase 2"
        />
      </div>
      {error && <p className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{error}</p>}
      {!review && !error && <p className="text-sm text-muted">Loading…</p>}
      {review?.drawing.error && (
        <p className="mb-5 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">The last run failed: {review.drawing.error}</p>
      )}
      {s && (
        <p className="mb-6 text-sm text-muted">
          {s.features ?? 0} features · {s.notes ?? 0} notes · {s.facts_found ?? 0} values read · {s.facts_not_found ?? 0} not printed · {s.facts_disputed ?? 0} disputed · {s.unbound_notes ?? 0} with no owner · {s.failures ?? 0} failures
        </p>
      )}
      {review?.sheets.map((sheet) => <Sheet key={sheet.sheet.id} review={sheet} />)}
    </div>
  );
}
