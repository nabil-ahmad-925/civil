import type { Manual } from "@/lib/documents/types";
import type { Classification, CrossRefStatus, RequirementClass } from "@/lib/standards/types";

/** One node in a list: where it sits, without its text. */
export type NodeSummary = {
  id: string;
  documentId: string;
  manual: Manual;
  chapter: string;
  section: string;
  /** The heading exactly as printed, e.g. "SECTION 1-1 GENERAL". */
  title: string;
  parentId: string | null;
  pageStart: number;
  pageEnd: number;
  ordinal: number;
  classification: Classification | null;
  /** 0 for a chapter, 1 for a section inside it, and so on. */
  depth: number;
};

/** A pointer to another node: enough to show it and jump to it. */
export type NodeRef = Pick<NodeSummary, "id" | "documentId" | "manual" | "section" | "title">;

/** A figure captured from a manual. Its picture is served at figureImageUrl(id). */
export type Figure = {
  id: string;
  documentId: string;
  manual: Manual;
  nodeId: string | null;
  /** e.g. "Figure 2-7" */
  label: string;
  caption: string;
  page: number;
};

export type NodeCondition = {
  id: string;
  kind: "applicability" | "exception";
  text: string;
  input: string | null;
  requiredInputs: string[];
};

export type RefOutgoing = {
  id: string;
  /** The words in this node's text that make the reference, as printed. */
  mention: string;
  targetManual: Manual | "OTHER";
  targetLabel: string;
  status: CrossRefStatus;
  resolvedBy: "internal_pass" | "cross_manual_pass" | null;
  /** Where it leads. Null until the reference is linked. */
  node: NodeRef | null;
  /** Set instead of `node` when the reference leads to a figure. */
  figure: Figure | null;
};

export type RefIncoming = {
  id: string;
  mention: string;
  status: CrossRefStatus;
  node: NodeRef;
};

export type NodeDetail = Omit<NodeSummary, "parentId" | "depth" | "classification"> & {
  /** Exactly as extracted from the PDF. */
  text: string;
  classification: Classification | null;
  requirementClass: RequirementClass | null;
  tags: string[];
  source: { id: string; manual: Manual; originalName: string; version: string; sha256: string; uploadedAt: string };
  parent: NodeRef | null;
  conditions: NodeCondition[];
  refsOut: RefOutgoing[];
  refsIn: RefIncoming[];
};

/** One row of the Cross-References page. */
export type CrossRefRow = {
  id: string;
  mention: string;
  targetManual: Manual | "OTHER";
  targetLabel: string;
  status: CrossRefStatus;
  resolvedBy: "internal_pass" | "cross_manual_pass" | null;
  /** The node whose text contains the mention. */
  source: NodeRef;
  /** Where it leads. Null until linked. */
  target: NodeRef | null;
  /** Set instead of `target` when the reference leads to a figure. */
  targetFigure: Figure | null;
};
