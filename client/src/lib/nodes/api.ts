import { API_URL, ApiError, errorMessage } from "@/lib/documents/api";
import type { CrossRefRow, Figure, NodeDetail, NodeSummary } from "./types";

/** The nodes of one document, in reading order, without text. `q`: words that must all appear in a node. */
export async function listNodes(documentId: string, q: string): Promise<NodeSummary[]> {
  const qs = new URLSearchParams({ document_id: documentId });
  if (q.trim()) qs.set("q", q.trim());
  const res = await fetch(`${API_URL}/api/nodes?${qs}`, { cache: "no-store" });
  if (!res.ok) throw new ApiError(await errorMessage(res, "Could not load nodes"));
  return res.json();
}

/** One node with its exact text, its source file, and its cross-references in both directions. */
export async function getNode(id: string): Promise<NodeDetail> {
  const res = await fetch(`${API_URL}/api/nodes/${id}`, { cache: "no-store" });
  if (!res.ok) throw new ApiError(await errorMessage(res, "Could not load the node"));
  return res.json();
}

/** The figures captured from one document, in page order. */
export async function listFigures(documentId: string): Promise<Figure[]> {
  const res = await fetch(`${API_URL}/api/figures?document_id=${encodeURIComponent(documentId)}`, { cache: "no-store" });
  if (!res.ok) throw new ApiError(await errorMessage(res, "Could not load figures"));
  return res.json();
}

/** Where a figure's PNG comes from: the API reads it from the storage bucket. */
export const figureImageUrl = (id: string) => `${API_URL}/api/figures/${id}/image`;

/** Every cross-reference of every ingested document, with the node it is in and the node it leads to. */
export async function listCrossRefs(): Promise<CrossRefRow[]> {
  const res = await fetch(`${API_URL}/api/cross-refs`, { cache: "no-store" });
  if (!res.ok) throw new ApiError(await errorMessage(res, "Could not load cross-references"));
  return res.json();
}
