"use client";

import Link from "next/link";
import { useState } from "react";
import { Card, ManualTag } from "@/components/standards/ui";
import { deleteDocument, ingestDocument, listDocuments } from "@/lib/documents/api";
import { formatBytes, type DocStatus, type DocumentRecord } from "@/lib/documents/types";

const statusStyles: Record<DocStatus, { label: string; cls: string }> = {
  uploaded: { label: "Not ingested yet", cls: "bg-mint text-muted border border-line" },
  processing: { label: "Ingesting…", cls: "bg-lime text-principal-deep animate-pulse" },
  ingested: { label: "Ingested", cls: "bg-principal text-blanco" },
  failed: { label: "Failed", cls: "bg-red-50 text-red-700 border border-red-200" },
};

export default function DocumentsTable({ initial }: { initial: DocumentRecord[] }) {
  const [docs, setDocs] = useState(initial);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function refresh() {
    try {
      setDocs(await listDocuments());
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not refresh the list.");
    }
  }

  async function ingest(d: DocumentRecord) {
    setBusyId(d.id);
    setError(null);
    // Show the row as ingesting while the request runs.
    setDocs((all) => all.map((x) => (x.id === d.id ? { ...x, status: "processing", error: null } : x)));
    try {
      await ingestDocument(d.id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Ingestion failed.");
    }
    await refresh();
    setBusyId(null);
  }

  async function remove(d: DocumentRecord) {
    if (!confirm(`Remove ${d.originalName}? The stored file and its chunks will be deleted.`)) return;
    setBusyId(d.id);
    setError(null);
    try {
      await deleteDocument(d.id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Delete failed.");
    }
    await refresh();
    setBusyId(null);
  }

  if (docs.length === 0) {
    return (
      <Card className="px-5 py-14 text-center">
        <p className="font-medium text-principal-deep">No manuals uploaded yet</p>
        <p className="mt-1 text-sm text-muted">Upload PPM, HDM and the CADD Users Manual to get started.</p>
        <Link
          href="/knowledge-base/upload"
          className="mt-5 inline-block rounded-xl bg-principal px-4 py-2.5 text-sm font-medium text-blanco transition hover:bg-principal-deep"
        >
          Upload a manual
        </Link>
      </Card>
    );
  }

  return (
    <>
      {error && (
        <p className="mb-3 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{error}</p>
      )}
      <Card className="overflow-x-auto p-0">
        <table className="w-full text-left text-sm">
          <thead className="border-b border-line bg-mint/50 text-xs uppercase tracking-wider text-muted">
            <tr>
              <th className="px-5 py-3 font-semibold">Manual</th>
              <th className="px-5 py-3 font-semibold">File</th>
              <th className="px-5 py-3 font-semibold">Version</th>
              <th className="px-5 py-3 font-semibold">Status</th>
              <th className="px-5 py-3 font-semibold">Uploaded</th>
              <th className="px-5 py-3" />
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            {docs.map((d) => {
              const busy = busyId === d.id || d.status === "processing";
              const s = d.summary;
              return (
                <tr key={d.id} className="align-top transition hover:bg-fondo">
                  <td className="px-5 py-3">
                    <ManualTag id={d.manual} />
                  </td>
                  <td className="px-5 py-3">
                    <p className="max-w-[260px] truncate font-medium text-texto">{d.originalName}</p>
                    <p className="text-xs text-muted">
                      {formatBytes(d.sizeBytes)} · <span className="font-mono">#{d.sha256.slice(0, 8)}</span>
                    </p>
                  </td>
                  <td className="px-5 py-3 text-texto">{d.version}</td>
                  <td className="px-5 py-3">
                    <span className={`rounded-full px-2.5 py-0.5 text-xs font-medium ${statusStyles[d.status].cls}`}>
                      {statusStyles[d.status].label}
                    </span>
                    {d.status === "ingested" && s && (
                      <p className="mt-1 text-xs text-muted">
                        {s.nodes} chunks · {s.cross_refs} references
                      </p>
                    )}
                    {d.status === "failed" && d.error && (
                      <p className="mt-1 max-w-[280px] text-xs text-red-700">{d.error}</p>
                    )}
                  </td>
                  <td className="whitespace-nowrap px-5 py-3 text-xs text-muted">
                    {new Date(d.uploadedAt).toLocaleString()}
                  </td>
                  <td className="whitespace-nowrap px-5 py-3 text-right text-sm">
                    {d.status !== "processing" && (
                      <button
                        onClick={() => ingest(d)}
                        disabled={busy}
                        className="mr-4 font-medium text-principal hover:underline disabled:opacity-50"
                      >
                        {d.status === "failed" ? "Retry ingest" : d.status === "ingested" ? "Re-ingest" : "Ingest"}
                      </button>
                    )}
                    <button onClick={() => remove(d)} disabled={busy} className="text-muted hover:text-red-700 disabled:opacity-50">
                      Remove
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </Card>
    </>
  );
}
