import type { Manual } from "@/lib/documents/types";

export type ChatFigure = { id: string; label: string; caption: string; page: number; /** Temporary link to the picture. */ url: string };

/** A section the answer was built from. `ref` is the [S1]-style marker used in the answer text. */
export type ChatSource = {
  ref: string;
  id: string;
  manual: Manual;
  section: string;
  title: string;
  pageStart: number;
  pageEnd: number;
  score: number | null;
  /** match: found by the question. mentions / mentioned_by: brought along through a cross-reference of `via`. */
  relation: "match" | "mentions" | "mentioned_by";
  via: string | null;
  mention: string | null;
  /** For a direct match: which searches found it (meaning, keywords, heading). */
  matchedBy: string[];
  path: string[];
  excerpt: string;
  figures: ChatFigure[];
};

export type ChatReply = { answer: string; covered: boolean; model: string | null; sources: ChatSource[] };
export type ChatTurn = { role: "user" | "assistant"; content: string };
