"use client";

import Link from "next/link";
import { useRef, useState } from "react";
import { Card, ManualTag, PageHeader } from "@/components/standards/ui";
import { ingestDocument, uploadDocument } from "@/lib/documents/api";
import {
  MANUALS,
  MAX_MANUAL_CHARS,
  MAX_UPLOAD_BYTES,
  formatBytes,
  type DocumentRecord,
  type Manual,
} from "@/lib/documents/types";

type Phase = "idle" | "uploading" | "ingesting" | "done" | "ingest_failed";

function guessManual(name: string): Manual | null {
  const n = name.toLowerCase();
  if (n.includes("ppm") || n.includes("plans prep")) return "PPM";
  if (n.includes("hdm") || n.includes("highway design")) return "HDM";
  if (n.includes("cadd")) return "CADD";
  return null;
}

type StepState = "pending" | "active" | "done" | "failed";

function Step({ n, label, detail, state }: { n: number; label: string; detail?: string; state: StepState }) {
  return (
    <li className="flex items-start gap-3">
      <span
        className={`mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-xs font-bold ${
          state === "done"
            ? "bg-principal text-blanco"
            : state === "active"
              ? "animate-pulse bg-lime text-principal-deep ring-4 ring-lime/40"
              : state === "failed"
                ? "bg-red-100 text-red-700"
                : "border-2 border-line text-muted"
        }`}
      >
        {state === "done" ? "✓" : state === "failed" ? "!" : n}
      </span>
      <div className="min-w-0">
        <p className={`text-sm font-medium ${state === "pending" ? "text-muted" : "text-texto"}`}>{label}</p>
        {detail && <p className="text-xs text-muted">{detail}</p>}
      </div>
    </li>
  );
}

export default function UploadPage() {
  const [file, setFile] = useState<File | null>(null);
  const [manual, setManual] = useState<Manual | null>(null);
  // The fourth choice: a document without numbered headings, uploaded under a name the user types.
  const [other, setOther] = useState(false);
  const [otherName, setOtherName] = useState("");
  const [version, setVersion] = useState("");
  const [dragging, setDragging] = useState(false);
  const [phase, setPhase] = useState<Phase>("idle");
  const [progress, setProgress] = useState(0);
  const [doc, setDoc] = useState<DocumentRecord | null>(null);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const chosenManual = other ? otherName.replace(/\s+/g, " ").trim() : manual;
  const busy = phase === "uploading" || phase === "ingesting";
  const started = phase !== "idle";

  function pick(f: File | undefined) {
    setError(null);
    if (!f || busy) return;
    if (!f.name.toLowerCase().endsWith(".pdf") && f.type !== "application/pdf") {
      setError("Only PDF files can be ingested.");
      return;
    }
    if (f.size > MAX_UPLOAD_BYTES) {
      setError(`File is ${formatBytes(f.size)}; the limit is ${formatBytes(MAX_UPLOAD_BYTES)}.`);
      return;
    }
    setFile(f);
    const guess = guessManual(f.name);
    if (guess && !other) setManual(guess);
    if (inputRef.current) inputRef.current.value = "";
  }

  async function runIngest(id: string) {
    setPhase("ingesting");
    setError(null);
    try {
      setDoc(await ingestDocument(id));
      setPhase("done");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Ingestion failed.");
      setPhase("ingest_failed");
    }
  }

  async function start() {
    if (!file || !chosenManual || busy) return;
    setError(null);
    setProgress(0);
    setPhase("uploading");
    let uploaded: DocumentRecord;
    try {
      uploaded = await uploadDocument(file, { manual: chosenManual, version: version.trim() }, setProgress);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Upload failed.");
      setPhase("idle");
      return;
    }
    setDoc(uploaded);
    await runIngest(uploaded.id);
  }

  function reset() {
    setFile(null);
    setManual(null);
    setOther(false);
    setOtherName("");
    setVersion("");
    setDoc(null);
    setError(null);
    setProgress(0);
    setPhase("idle");
  }

  const uploadState: StepState = phase === "uploading" ? "active" : phase === "idle" ? "pending" : "done";
  const ingestState: StepState =
    phase === "ingesting" ? "active" : phase === "done" ? "done" : phase === "ingest_failed" ? "failed" : "pending";
  const s = doc?.summary;

  return (
    <div className="mx-auto max-w-3xl px-8 py-10">
      <PageHeader
        title="Upload a Manual"
        subtitle="Pick one PDF. It is saved to storage first, then cut into chunks and saved to the database."
        sampleData={false}
      />

      <Card className="p-6">
        {!started && (
          <>
            <div
              role="button"
              tabIndex={0}
              onClick={() => inputRef.current?.click()}
              onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && inputRef.current?.click()}
              onDragOver={(e) => {
                e.preventDefault();
                setDragging(true);
              }}
              onDragLeave={() => setDragging(false)}
              onDrop={(e) => {
                e.preventDefault();
                setDragging(false);
                pick(e.dataTransfer.files[0]);
              }}
              className={`flex cursor-pointer flex-col items-center justify-center rounded-2xl border-2 border-dashed px-6 py-10 text-center transition ${
                dragging ? "border-principal bg-lime/40" : "border-line bg-fondo hover:border-principal/50 hover:bg-mint/50"
              }`}
            >
              <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-principal text-lime shadow-md shadow-principal/30">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} className="h-6 w-6">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M12 16.5V3.75m0 0L7.5 8.25M12 3.75l4.5 4.5M3.75 15v3A2.25 2.25 0 0 0 6 20.25h12A2.25 2.25 0 0 0 20.25 18v-3" />
                </svg>
              </div>
              <p className="mt-3 font-semibold text-principal-deep">Drop a manual PDF here</p>
              <p className="mt-1 text-sm text-muted">or click to browse · PDF up to {formatBytes(MAX_UPLOAD_BYTES)}</p>
              <input
                ref={inputRef}
                type="file"
                accept="application/pdf,.pdf"
                className="hidden"
                onChange={(e) => pick(e.target.files?.[0])}
              />
            </div>

            {file && (
              <div className="mt-4 flex items-center justify-between gap-3 rounded-xl border border-line bg-mint/40 px-4 py-3">
                <div className="flex min-w-0 items-center gap-3">
                  <span className="rounded-md bg-principal-deep px-1.5 py-0.5 text-[11px] font-bold text-lime">PDF</span>
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium text-texto">{file.name}</p>
                    <p className="text-xs text-muted">{formatBytes(file.size)}</p>
                  </div>
                </div>
                <button onClick={() => setFile(null)} className="text-xs text-muted hover:text-principal-deep">
                  Remove
                </button>
              </div>
            )}

            <div className="mt-6">
              <p className="mb-2 text-sm font-medium text-texto">Which manual is this?</p>
              <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
                {MANUALS.map((m) => (
                  <button
                    key={m.id}
                    type="button"
                    onClick={() => {
                      setOther(false);
                      setManual(m.id);
                    }}
                    className={`rounded-xl border px-4 py-3 text-left transition ${
                      !other && manual === m.id ? "border-principal bg-mint ring-2 ring-lime" : "border-line bg-blanco hover:border-principal/40"
                    }`}
                  >
                    <ManualTag id={m.id} />
                    <p className="mt-1.5 text-sm text-texto">{m.label}</p>
                  </button>
                ))}
                <button
                  type="button"
                  onClick={() => setOther(true)}
                  className={`rounded-xl border px-4 py-3 text-left transition ${
                    other ? "border-principal bg-mint ring-2 ring-lime" : "border-line bg-blanco hover:border-principal/40"
                  }`}
                >
                  <span className="rounded-md border border-principal-deep px-1.5 py-0.5 font-mono text-[11px] font-semibold text-principal-deep">
                    Other
                  </span>
                  <p className="mt-1.5 text-sm text-texto">Another document: type its name</p>
                </button>
              </div>
              {other && (
                <div className="mt-3">
                  <label htmlFor="manual-name" className="mb-2 block text-sm font-medium text-texto">
                    Name of the manual
                  </label>
                  <input
                    id="manual-name"
                    value={otherName}
                    onChange={(e) => setOtherName(e.target.value)}
                    maxLength={MAX_MANUAL_CHARS}
                    autoFocus
                    placeholder="e.g. Drainage Memo"
                    className="w-full rounded-xl border border-line bg-blanco px-4 py-3 text-sm outline-none transition placeholder:text-muted/60 focus:border-principal focus:ring-4 focus:ring-lime/70"
                  />
                  <p className="mt-1.5 text-xs text-muted">
                    For a document with no numbered headings. A model reads it page by page and splits it into topics. The same
                    name on a later upload is the same manual.
                  </p>
                </div>
              )}
            </div>

            <div className="mt-5">
              <label htmlFor="version" className="mb-2 block text-sm font-medium text-texto">
                Version / edition <span className="font-normal text-muted">(optional)</span>
              </label>
              <input
                id="version"
                value={version}
                onChange={(e) => setVersion(e.target.value)}
                placeholder="e.g. 7th Edition, July 2024 update"
                className="w-full rounded-xl border border-line bg-blanco px-4 py-3 text-sm outline-none transition placeholder:text-muted/60 focus:border-principal focus:ring-4 focus:ring-lime/70"
              />
            </div>

            <button
              onClick={start}
              disabled={!file || !chosenManual}
              className="mt-6 w-full rounded-xl bg-principal px-5 py-3 text-sm font-medium text-blanco shadow-md shadow-principal/25 transition hover:bg-principal-deep disabled:cursor-not-allowed disabled:opacity-50"
            >
              Upload and ingest
            </button>
          </>
        )}

        {started && (
          <>
            <div className="mb-5 flex items-center gap-3">
              {chosenManual && <ManualTag id={chosenManual} />}
              <p className="min-w-0 truncate text-sm font-medium text-texto">{file?.name}</p>
            </div>
            <ol className="space-y-4">
              <Step
                n={1}
                label="Save the PDF to storage"
                state={uploadState}
                detail={phase === "uploading" ? (progress === 100 ? "Saving to the bucket…" : `Uploading… ${progress}%`) : undefined}
              />
              <Step
                n={2}
                label="Cut into chunks and save to the database"
                state={ingestState}
                detail={phase === "ingesting" ? "This can take a while for a large manual." : undefined}
              />
            </ol>
            {phase === "uploading" && (
              <div className="mt-4 h-2 overflow-hidden rounded-full bg-mint">
                <div className="h-full rounded-full bg-principal transition-all" style={{ width: `${progress}%` }} />
              </div>
            )}
          </>
        )}

        {error && (
          <p className="mt-5 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{error}</p>
        )}

        {phase === "ingest_failed" && doc && (
          <div className="mt-5 flex flex-wrap items-center gap-4">
            <p className="text-sm text-muted">The file is saved. Fix the cause, then try the chunking again.</p>
            <button
              onClick={() => runIngest(doc.id)}
              className="rounded-xl bg-principal px-4 py-2 text-sm font-medium text-blanco transition hover:bg-principal-deep"
            >
              Retry ingest
            </button>
            <Link href="/knowledge-base" className="text-sm font-medium text-principal hover:underline">
              View in Knowledge Base
            </Link>
          </div>
        )}

        {phase === "done" && s && (
          <div className="mt-6 border-t border-line pt-5">
            <p className="font-semibold text-principal-deep">Ingested</p>
            <dl className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-4">
              {[
                ["Pages", s.pages],
                ["Chunks saved", s.nodes],
                ["Cross-references", s.cross_refs],
                ["Linked now", (s.internal_pass?.resolved ?? 0) + (s.cross_manual_pass?.resolved ?? 0)],
              ].map(([label, value]) => (
                <div key={label as string} className="rounded-xl border border-line bg-fondo px-3 py-2.5">
                  <dt className="text-xs text-muted">{label}</dt>
                  <dd className="text-xl font-bold text-principal-deep">{value ?? "—"}</dd>
                </div>
              ))}
            </dl>
            {typeof s.vectors === "string" && <p className="mt-3 text-xs text-muted">Vectors: {s.vectors}</p>}
            {(s.cross_manual_pass?.waiting_for_other_manual ?? 0) > 0 && (
              <p className="mt-1 text-xs text-muted">
                {s.cross_manual_pass?.waiting_for_other_manual} reference(s) point to a manual that is not uploaded yet. They
                link automatically when it is.
              </p>
            )}
            <div className="mt-5 flex items-center gap-4">
              <Link
                href="/knowledge-base"
                className="rounded-xl bg-principal px-4 py-2 text-sm font-medium text-blanco transition hover:bg-principal-deep"
              >
                View in Knowledge Base
              </Link>
              <button onClick={reset} className="text-sm font-medium text-principal hover:underline">
                Upload another
              </button>
            </div>
          </div>
        )}
      </Card>
    </div>
  );
}
