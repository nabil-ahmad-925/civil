"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { Card, PageHeader } from "@/components/standards/ui";
import { formatBytes, MAX_UPLOAD_BYTES } from "@/lib/documents/types";
import { deleteDrawing, listDrawings, processDrawing, uploadDrawing } from "@/lib/drawings/api";
import type { DrawingRecord } from "@/lib/drawings/types";

const STATUS: Record<DrawingRecord["status"], string> = {
  uploaded: "Not read yet",
  processing: "Reading…",
  processed: "Read",
  failed: "Failed",
};

export default function DrawingsPage() {
  const [drawings, setDrawings] = useState<DrawingRecord[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  async function load() {
    try {
      setDrawings(await listDrawings());
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load drawings.");
      setDrawings([]);
    }
  }

  useEffect(() => {
    load();
  }, []);

  async function run(id: string) {
    setBusy(id);
    setError(null);
    try {
      await processDrawing(id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Processing failed.");
    }
    await load();
    setBusy(null);
  }

  async function pick(file: File | undefined) {
    if (inputRef.current) inputRef.current.value = "";
    if (!file || busy) return;
    if (file.size > MAX_UPLOAD_BYTES) {
      setError(`File is ${formatBytes(file.size)}; the limit is ${formatBytes(MAX_UPLOAD_BYTES)}.`);
      return;
    }
    setError(null);
    setBusy("upload");
    try {
      const drawing = await uploadDrawing(file, file.name);
      await load();
      await run(drawing.id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Upload failed.");
      setBusy(null);
    }
  }

  async function remove(d: DrawingRecord) {
    if (!window.confirm(`Delete "${d.name}" and everything read from it?`)) return;
    setError(null);
    try {
      await deleteDrawing(d.id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Delete failed.");
    }
    await load();
  }

  return (
    <div className="mx-auto max-w-5xl px-8 py-10">
      <PageHeader
        title="Drawings"
        subtitle="Upload a drawing image. A vision model lists its features and notes and reads the printed values. Everything it finds is a proposal until an engineer verifies it."
        sampleData={false}
        eyebrow="Drawings · Phase 2"
      >
        <button
          onClick={() => inputRef.current?.click()}
          disabled={busy !== null}
          className="rounded-xl bg-principal px-4 py-2 text-sm font-medium text-blanco transition hover:bg-principal-deep disabled:cursor-not-allowed disabled:opacity-50"
        >
          {busy === "upload" ? "Uploading…" : "Upload a drawing"}
        </button>
        <input
          ref={inputRef}
          type="file"
          accept="image/png,image/jpeg,image/webp,image/tiff,.png,.jpg,.jpeg,.webp,.tif,.tiff"
          className="hidden"
          onChange={(e) => pick(e.target.files?.[0])}
        />
      </PageHeader>

      {error && <p className="mb-5 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{error}</p>}

      {drawings === null && <p className="text-sm text-muted">Loading…</p>}
      {drawings?.length === 0 && !error && (
        <Card>
          <p className="text-sm text-muted">No drawings yet. PNG, JPEG, WEBP or TIFF. Each page of a TIFF is one sheet.</p>
        </Card>
      )}

      <div className="space-y-3">
        {drawings?.map((d) => (
          <Card key={d.id} className="flex flex-wrap items-center justify-between gap-4">
            <div className="min-w-0">
              <p className="truncate font-semibold text-principal-deep">{d.name}</p>
              <p className="mt-1 text-xs text-muted">
                {d.sheets.length} sheet{d.sheets.length === 1 ? "" : "s"} · {busy === d.id ? "Reading…" : STATUS[d.status]}
                {d.summary && (
                  <>
                    {" "}· {d.summary.features ?? 0} features · {d.summary.facts_found ?? 0} values read
                    {d.summary.estimated_usd != null && ` · about $${d.summary.estimated_usd.toFixed(2)}`}
                  </>
                )}
              </p>
              {d.error && <p className="mt-1 text-xs text-red-700">{d.error}</p>}
            </div>
            <div className="flex items-center gap-4">
              <Link href={`/drawings/${d.id}`} className="text-sm font-medium text-principal hover:underline">
                Review
              </Link>
              <button
                onClick={() => run(d.id)}
                disabled={busy !== null}
                className="text-sm font-medium text-principal hover:underline disabled:cursor-not-allowed disabled:opacity-50"
              >
                {d.status === "uploaded" ? "Read" : "Read again"}
              </button>
              <button
                onClick={() => remove(d)}
                disabled={busy !== null}
                className="text-sm text-muted hover:text-red-700 disabled:cursor-not-allowed disabled:opacity-50"
              >
                Delete
              </button>
            </div>
          </Card>
        ))}
      </div>
    </div>
  );
}
