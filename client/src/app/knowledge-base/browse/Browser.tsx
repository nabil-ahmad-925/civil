"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import NodeDetailView from "@/components/standards/NodeDetail";
import { Card, ClassificationBadge, PageHeader } from "@/components/standards/ui";
import { API_URL, ApiError, listDocuments } from "@/lib/documents/api";
import { MANUALS, manualTabs, type DocumentRecord, type Manual } from "@/lib/documents/types";
import { getNode, listNodes } from "@/lib/nodes/api";
import type { NodeDetail, NodeRef, NodeSummary } from "@/lib/nodes/types";
import type { Classification } from "@/lib/standards/types";

type Filter = "all" | Classification | "untagged";

const filters: { id: Filter; label: string }[] = [
  { id: "all", label: "All" },
  { id: "requirement", label: "Requirements" },
  { id: "guidance", label: "Guidance" },
  { id: "non_governing", label: "Non-governing" },
  { id: "untagged", label: "Untagged" },
];

type Failure = { kind: "unreachable" } | { kind: "server"; message: string };
/** The answer to one request. `key` says which request it was for, so a late answer to an old one is ignored. */
type Answer<T> = { key: string; data?: T; failure?: Failure };

function toFailure(e: unknown): Failure {
  return e instanceof ApiError ? { kind: "server", message: e.message } : { kind: "unreachable" };
}

function FailureNote({ failure }: { failure: Failure }) {
  return (
    <div className="rounded-xl border border-amber-200 bg-amber-50/60 px-5 py-6 text-center text-sm text-amber-900">
      {failure.kind === "server" ? (
        <>
          <p className="font-semibold">The server returned an error</p>
          <p className="mt-1 text-amber-800">{failure.message}</p>
        </>
      ) : (
        <>
          <p className="font-semibold">Cannot reach the server</p>
          <p className="mt-1 text-amber-800">
            Nothing answered at <span className="font-mono">{API_URL}</span>. Start the backend, then reload.
          </p>
        </>
      )}
    </div>
  );
}

export default function Browser({ initialManual, initialNode }: { initialManual: Manual; initialNode?: string }) {
  const [docsAnswer, setDocsAnswer] = useState<Answer<DocumentRecord[]> | null>(null);
  const [manual, setManual] = useState<Manual>(initialManual);
  const [pickedDocId, setPickedDocId] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [search, setSearch] = useState(""); // `query`, once typing has paused
  const [filter, setFilter] = useState<Filter>("all");
  const [selectedId, setSelectedId] = useState<string | null>(initialNode ?? null);
  const [nodesAnswer, setNodesAnswer] = useState<Answer<NodeSummary[]> | null>(null);
  const [detailAnswer, setDetailAnswer] = useState<Answer<NodeDetail> | null>(null);

  // 1. Which manuals are ingested.
  useEffect(() => {
    let stale = false;
    listDocuments()
      .then((data) => !stale && setDocsAnswer({ key: "docs", data }))
      .catch((e) => !stale && setDocsAnswer({ key: "docs", failure: toFailure(e) }));
    return () => {
      stale = true;
    };
  }, []);

  useEffect(() => {
    const timer = setTimeout(() => setSearch(query.trim()), 250);
    return () => clearTimeout(timer);
  }, [query]);

  // The document being browsed: the one picked, else the most recent ingested upload of this manual.
  const manualDocs = useMemo(
    () =>
      (docsAnswer?.data ?? [])
        .filter((d) => d.status === "ingested" && d.manual === manual)
        .sort((a, b) => b.uploadedAt.localeCompare(a.uploadedAt)),
    [docsAnswer, manual],
  );
  const activeDoc = manualDocs.find((d) => d.id === pickedDocId) ?? manualDocs[0] ?? null;
  const activeDocId = activeDoc?.id ?? null;

  // 2. The nodes of that document, filtered by the search words on the server.
  const nodesKey = activeDocId ? `${activeDocId}|${search}` : null;
  useEffect(() => {
    if (!activeDocId || !nodesKey) return;
    let stale = false;
    listNodes(activeDocId, search)
      .then((data) => !stale && setNodesAnswer({ key: nodesKey, data }))
      .catch((e) => !stale && setNodesAnswer({ key: nodesKey, failure: toFailure(e) }));
    return () => {
      stale = true;
    };
  }, [activeDocId, search, nodesKey]);
  const nodes = nodesAnswer?.key === nodesKey ? nodesAnswer : null; // null while the request is in flight

  const visible = useMemo(
    () =>
      (nodes?.data ?? []).filter((n) =>
        filter === "all" ? true : filter === "untagged" ? n.classification === null : n.classification === filter,
      ),
    [nodes, filter],
  );

  // The node shown on the right: the one clicked if it is in the list, else the first one.
  const shownId = useMemo(() => {
    if (!nodes?.data) return selectedId;
    return selectedId && visible.some((n) => n.id === selectedId) ? selectedId : (visible[0]?.id ?? null);
  }, [nodes, visible, selectedId]);

  // 3. That node, with its exact text and cross-references.
  useEffect(() => {
    if (!shownId) return;
    let stale = false;
    getNode(shownId)
      .then((data) => !stale && setDetailAnswer({ key: shownId, data }))
      .catch((e) => !stale && setDetailAnswer({ key: shownId, failure: toFailure(e) }));
    return () => {
      stale = true;
    };
  }, [shownId]);
  const detail = shownId && detailAnswer?.key === shownId ? detailAnswer : null;

  function chooseManual(m: Manual) {
    setManual(m);
    setPickedDocId(null);
    setSelectedId(null);
  }

  function chooseDocument(id: string) {
    setPickedDocId(id);
    setSelectedId(null);
  }

  /** Follow a cross-reference, into another manual if that is where it leads. */
  function openRef(ref: NodeRef) {
    setManual(ref.manual);
    setPickedDocId(ref.documentId);
    setQuery("");
    setSearch("");
    setFilter("all");
    setSelectedId(ref.id);
  }

  const ingestedManuals = new Set((docsAnswer?.data ?? []).filter((d) => d.status === "ingested").map((d) => d.manual));
  const manualHasUploads = (docsAnswer?.data ?? []).some((d) => d.manual === manual);

  return (
    <div className="mx-auto max-w-7xl px-8 py-10">
      <PageHeader
        title="Browse Nodes"
        subtitle="Every node keeps its exact source text, its citation and its cross-references."
        sampleData={false}
      />

      <div className="mb-4 flex flex-wrap items-center justify-between gap-4">
        <div className="inline-flex rounded-xl border border-line bg-blanco p-1 shadow-sm">
          {manualTabs((docsAnswer?.data ?? []).map((d) => d.manual)).map((id) => (
            <button
              key={id}
              onClick={() => chooseManual(id)}
              title={MANUALS.find((m) => m.id === id)?.label ?? id}
              className={`rounded-lg px-4 py-2 text-sm font-medium transition ${
                id === manual ? "bg-principal text-blanco shadow" : "text-texto hover:bg-mint"
              }`}
            >
              {id}
              {docsAnswer?.data && !ingestedManuals.has(id) && <span className="ml-1.5 text-xs opacity-60">–</span>}
            </button>
          ))}
        </div>
        {manualDocs.length > 1 && (
          <label className="flex items-center gap-2 text-sm text-muted">
            Document
            <select
              value={activeDocId ?? ""}
              onChange={(e) => chooseDocument(e.target.value)}
              className="max-w-[320px] rounded-lg border border-line bg-blanco px-3 py-2 text-sm text-texto outline-none focus:border-principal"
            >
              {manualDocs.map((d) => (
                <option key={d.id} value={d.id}>
                  {d.originalName} · {d.version} · {new Date(d.uploadedAt).toLocaleDateString()}
                </option>
              ))}
            </select>
          </label>
        )}
      </div>

      {!docsAnswer ? (
        <Card className="px-5 py-12 text-center text-sm text-muted">Loading…</Card>
      ) : docsAnswer.failure ? (
        <FailureNote failure={docsAnswer.failure} />
      ) : !activeDoc ? (
        <Card className="px-5 py-12 text-center">
          <p className="font-medium text-principal-deep">
            {manualHasUploads ? `${manual} is uploaded but not ingested yet` : `No ${manual} has been uploaded yet`}
          </p>
          <p className="mt-1 text-sm text-muted">
            {manualHasUploads ? "Run Ingest on it from the Knowledge Base page." : "Upload it to see its nodes here."}
          </p>
          <Link
            href={manualHasUploads ? "/knowledge-base" : "/knowledge-base/upload"}
            className="mt-5 inline-block rounded-xl bg-principal px-4 py-2.5 text-sm font-medium text-blanco transition hover:bg-principal-deep"
          >
            {manualHasUploads ? "Go to Knowledge Base" : "Upload a manual"}
          </Link>
        </Card>
      ) : (
        <>
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={`Search ${manual} by heading or text. Every word must match...`}
            className="mb-3 w-full rounded-xl border border-line bg-blanco px-4 py-3 text-sm shadow-sm outline-none transition placeholder:text-muted/60 focus:border-principal focus:ring-4 focus:ring-lime/70"
          />
          <div className="mb-6 flex flex-wrap gap-1.5">
            {filters.map((f) => (
              <button
                key={f.id}
                onClick={() => setFilter(f.id)}
                className={`rounded-full px-3 py-1 text-xs font-medium transition ${
                  f.id === filter ? "bg-principal-deep text-lime" : "bg-mint text-principal-deep hover:bg-lime"
                }`}
              >
                {f.label}
              </button>
            ))}
          </div>

          <div className="grid gap-6 lg:grid-cols-[380px_1fr]">
            <Card className="max-h-[70vh] overflow-y-auto p-3">
              {!nodes ? (
                <p className="p-4 text-center text-sm text-muted">Loading nodes…</p>
              ) : nodes.failure ? (
                <FailureNote failure={nodes.failure} />
              ) : (
                <>
                  <p className="px-2 pb-2 text-xs text-muted">
                    {visible.length} node{visible.length === 1 ? "" : "s"}
                    {search ? ` matching “${search}”` : ""}
                  </p>
                  {visible.map((n) => (
                    <button
                      key={n.id}
                      onClick={() => setSelectedId(n.id)}
                      style={{ paddingLeft: 12 + n.depth * 16 }}
                      className={`mb-1 w-full rounded-xl py-2.5 pr-3 text-left transition ${
                        shownId === n.id ? "bg-mint ring-1 ring-principal/30" : "hover:bg-fondo"
                      }`}
                    >
                      <span className="block text-sm font-medium text-texto">{n.title}</span>
                      <span className="mt-0.5 flex items-center gap-2 text-xs text-muted">
                        {n.pageStart === n.pageEnd ? `p. ${n.pageStart}` : `pp. ${n.pageStart}–${n.pageEnd}`}
                        {n.classification && <ClassificationBadge value={n.classification} />}
                      </span>
                    </button>
                  ))}
                  {visible.length === 0 && (
                    <p className="p-4 text-center text-sm text-muted">
                      {search ? `No nodes match “${search}”.` : "No nodes match this filter."}
                    </p>
                  )}
                </>
              )}
            </Card>

            <Card className="p-7">
              {!shownId ? (
                <p className="text-muted">{nodes ? "Select a node." : "Loading…"}</p>
              ) : !detail ? (
                <p className="text-muted">Loading…</p>
              ) : detail.failure ? (
                <FailureNote failure={detail.failure} />
              ) : (
                detail.data && <NodeDetailView detail={detail.data} onOpen={openRef} />
              )}
            </Card>
          </div>
        </>
      )}
    </div>
  );
}
