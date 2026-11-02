export type DrawingStatus = "uploaded" | "processing" | "processed" | "failed";
export type Confidence = "high" | "medium" | "low";
/** x0, y0, x1, y1 in pixels of the sheet image. */
export type Box = [number, number, number, number];

export type DrawingSheet = {
  id: string;
  drawingId: string;
  number: number;
  originalName: string;
  width: number;
  height: number;
  warnings: string[];
  failures: string[];
};

/** Counts and model usage of the last run. Keys are the server's (snake_case). */
export type DrawingSummary = {
  features?: number;
  notes?: number;
  unbound_notes?: number;
  facts_found?: number;
  facts_not_found?: number;
  facts_disputed?: number;
  failures?: number;
  estimated_usd?: number;
};

export type DrawingRecord = {
  id: string;
  name: string;
  status: DrawingStatus;
  error: string | null;
  summary: DrawingSummary | null;
  uploadedAt: string;
  sheets: DrawingSheet[];
};

export type DrawingFeature = {
  id: string;
  type: string;
  label: string;
  bbox: Box | null;
  confidence: Confidence;
  foundBy: "sheet" | "tile";
  related: { featureId: string; relation: string }[];
  reviewState: string;
};

export type DrawingNote = {
  id: string;
  text: string;
  bbox: Box | null;
  confidence: Confidence;
  scope: "feature" | "sheet" | "unbound";
  foundBy: "sheet" | "tile";
  reviewState: string;
};

export type NoteBinding = {
  id: string;
  noteId: string;
  featureId: string;
  evidence: "leader_line" | "adjacent" | "label";
  confidence: Confidence;
  hasEvidence: boolean;
  reviewState: string;
};

export type DrawingFact = {
  id: string;
  featureId: string | null;
  term: string;
  status: "FOUND" | "NOT_FOUND";
  kind: string;
  value: number | string | null;
  unit: string;
  verbatimText: string;
  bbox: Box | null;
  confidence: Confidence;
  confirmation: "confirmed" | "disputed" | "not_checked";
  hasEvidence: boolean;
  reviewState: string;
};

export type ReviewItem = { kind: string; message: string; targetId: string | null };

export type SheetReview = {
  sheet: DrawingSheet;
  features: DrawingFeature[];
  notes: DrawingNote[];
  bindings: NoteBinding[];
  facts: DrawingFact[];
  needsReview: ReviewItem[];
};

export type DrawingReview = { drawing: DrawingRecord; sheets: SheetReview[] };
