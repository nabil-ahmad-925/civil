export const MANUALS = [
  { id: "PPM", label: "Plans Preparation Manual" },
  { id: "HDM", label: "Highway Design Manual" },
  { id: "CADD", label: "CADD Users Manual" },
] as const;

export type Manual = (typeof MANUALS)[number]["id"];

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
