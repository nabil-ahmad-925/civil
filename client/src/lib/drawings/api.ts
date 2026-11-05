import { API_URL, ApiError, errorMessage } from "@/lib/documents/api";
import type { DrawingRecord, DrawingReview } from "./types";

export async function listDrawings(): Promise<DrawingRecord[]> {
  const res = await fetch(`${API_URL}/api/drawings`, { cache: "no-store" });
  if (!res.ok) throw new ApiError(await errorMessage(res, "Could not load drawings"));
  return res.json();
}

/** Step 1: save the image. One sheet, or one per page of a TIFF. Nothing is read yet. */
export async function uploadDrawing(file: File, name: string): Promise<DrawingRecord> {
  const qs = new URLSearchParams({ filename: file.name, name });
  const res = await fetch(`${API_URL}/api/drawings?${qs}`, { method: "POST", body: file });
  if (!res.ok) throw new ApiError(await errorMessage(res, "Upload failed"));
  return res.json();
}

export async function addSheets(id: string, file: File): Promise<DrawingRecord> {
  const qs = new URLSearchParams({ filename: file.name });
  const res = await fetch(`${API_URL}/api/drawings/${id}/sheets?${qs}`, { method: "POST", body: file });
  if (!res.ok) throw new ApiError(await errorMessage(res, "Upload failed"));
  return res.json();
}

/** Step 2: the vision model reads every sheet. Can take minutes. */
export async function processDrawing(id: string): Promise<DrawingRecord> {
  const res = await fetch(`${API_URL}/api/drawings/${id}/process`, { method: "POST" });
  if (!res.ok) throw new ApiError(await errorMessage(res, "Processing failed"));
  return res.json();
}

export async function getReview(id: string): Promise<DrawingReview> {
  const res = await fetch(`${API_URL}/api/drawings/${id}/review`, { cache: "no-store" });
  if (!res.ok) throw new ApiError(await errorMessage(res, "Could not load the review"));
  return res.json();
}

export async function deleteDrawing(id: string): Promise<void> {
  const res = await fetch(`${API_URL}/api/drawings/${id}`, { method: "DELETE" });
  if (!res.ok && res.status !== 404) throw new ApiError(await errorMessage(res, "Delete failed"));
}

export const sheetImageUrl = (sheetId: string) => `${API_URL}/api/drawings/sheets/${sheetId}/image`;
export const factEvidenceUrl = (factId: string) => `${API_URL}/api/drawings/facts/${factId}/evidence`;
export const bindingEvidenceUrl = (bindingId: string) => `${API_URL}/api/drawings/bindings/${bindingId}/evidence`;
