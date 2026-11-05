export const MANUALS = [
  { id: "PPM", label: "Plans Preparation Manual" },
  { id: "HDM", label: "Highway Design Manual" },
  { id: "CADD", label: "CADD Users Manual" },
] as const;

/** PPM, HDM and CADD have numbered headings. Any other manual is a name the user typed at upload. */
export type CoreManual = (typeof MANUALS)[number]["id"];
export type Manual = string;

export const MAX_MANUAL_CHARS = 80;

/** PPM, HDM, CADD, then the other manual names that exist, A to Z. */
export function manualTabs(names: Iterable<string>): string[] {
  const core: string[] = MANUALS.map((m) => m.id);
  const others = [...new Set(names)].filter((n) => !core.includes(n)).sort((a, b) => a.localeCompare(b));
  return [...core, ...others];
}

/** uploaded: in the bucket, not chunked yet. failed: ingest raised, see `error`. */
export type DocStatus = "uploaded" | "processing" | "ingested" | "failed";

export type DocumentRecord = {
  id: string;
  manual: Manual;
  version: string;
  originalName: string;
  sizeBytes: number;
  sha256: string;
  uploadedAt: string;
  status: DocStatus;
  error: string | null;
  /** Counts from the last ingest. Keys are the server's (snake_case). */
  summary: IngestSummary | null;
};

export type IngestSummary = {
  pages?: number;
  nodes?: number;
  cross_refs?: number;
  vectors?: number | string;
  internal_pass?: { resolved: number; still_unresolved: number };
  cross_manual_pass?: { resolved: number; unresolved: number; waiting_for_other_manual: number };
};

export const MAX_UPLOAD_BYTES = 300 * 1024 * 1024;

export function formatBytes(n: number) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}
