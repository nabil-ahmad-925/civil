import type { DocumentRecord } from "./types";

/** The FastAPI server. Set NEXT_PUBLIC_API_URL in client/.env.local to change it. */
export const API_URL = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(/\/$/, "");

/** The server answered, but with an error. (A thrown TypeError instead means it could not be reached.) */
export class ApiError extends Error {}

export async function errorMessage(res: Response, fallback: string) {
  const body = await res.json().catch(() => null);
  return body?.error ?? `${fallback} (${res.status}).`;
}

export async function listDocuments(): Promise<DocumentRecord[]> {
  const res = await fetch(`${API_URL}/api/documents`, { cache: "no-store" });
  if (!res.ok) throw new ApiError(await errorMessage(res, "Could not load documents"));
  return res.json();
}

/** Step 2: chunk the stored PDF and save nodes, cross-references and vectors. */
export async function ingestDocument(id: string): Promise<DocumentRecord> {
  const res = await fetch(`${API_URL}/api/documents/${id}/ingest`, { method: "POST" });
  if (!res.ok) throw new ApiError(await errorMessage(res, "Ingestion failed"));
  return res.json();
}

export async function deleteDocument(id: string): Promise<void> {
  const res = await fetch(`${API_URL}/api/documents/${id}`, { method: "DELETE" });
  if (!res.ok && res.status !== 404) throw new ApiError(await errorMessage(res, "Delete failed"));
}

/** Step 1: send the PDF to the server, which saves it in the Supabase bucket. Reports upload progress. */
export function uploadDocument(
  file: File,
  meta: { manual: string; version: string },
  onProgress: (percent: number) => void,
): Promise<DocumentRecord> {
  return new Promise((resolve, reject) => {
    const qs = new URLSearchParams({ manual: meta.manual, version: meta.version, filename: file.name });
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${API_URL}/api/documents?${qs}`);
    xhr.setRequestHeader("Content-Type", "application/pdf");
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) onProgress(Math.round((e.loaded / e.total) * 100));
    };
    xhr.onload = () => {
      let body: { error?: string } | null = null;
      try {
        body = JSON.parse(xhr.responseText);
      } catch {}
      if (xhr.status === 201) resolve(body as unknown as DocumentRecord);
      else reject(new Error(body?.error ?? `Upload failed (${xhr.status}).`));
    };
    xhr.onerror = () => reject(new Error(`Cannot reach the server at ${API_URL}. Is it running?`));
    xhr.send(file);
  });
}
