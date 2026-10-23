export type ManualId = "PPM" | "HDM" | "CADD";

export type StageStatus = "done" | "running" | "pending" | "blocked";

export type Stage = {
  status: StageStatus;
  done: number;
  total: number;
  note?: string;
};

export type ManualPipeline = {
  chunking: Stage;
  internalRefs: Stage;
  vectors: Stage;
};

export type Chapter = {
  id: string;
  number: string;
  title: string;
};

export type Manual = {
  id: ManualId;
  title: string;
  fullTitle: string;
  version: string;
  sourceHash: string;
  chapters: Chapter[];
  pipeline: ManualPipeline;
};

/** Content classification assigned during tagging. */
export type Classification = "requirement" | "guidance" | "non_governing";

/** Read from the HDM source formatting; never guessed. */
export type RequirementClass = "boldface" | "underlined" | "approval_required" | "permissive";

export type Condition = {
  id: string;
  /** Input the condition depends on, e.g. "posted_speed_mph". */
  input: string;
  text: string;
};

export type Exception = {
  id: string;
  text: string;
  /** Inputs that must be known before the exception can be evaluated. */
  requiredInputs: string[];
};

export type StandardNode = {
  id: string;
  manual: ManualId;
  chapterId: string;
  section: string;
  title: string;
  page: number;
  /** Exact source text, never rewritten. */
  text: string;
  embedded: boolean;
  /** null until the tagging pass has reached this node. */
  classification: Classification | null;
  requirementClass?: RequirementClass;
  tags: string[];
  applicability: Condition[];
  exceptions: Exception[];
};

/**
 * resolved           – linked to a real node
 * unresolved         – same-manual target not found yet (internal pass pending or failed)
 * awaiting_cross     – points at another manual; waits for the final cross-manual pass
 */
/** external: points at a document that is not PPM, HDM or CADD; kept as text, never linked. */
export type CrossRefStatus = "resolved" | "unresolved" | "awaiting_cross" | "external";

export type CrossRef = {
  id: string;
  fromNodeId: string;
  /** Verbatim mention as captured while chunking. */
  mention: string;
  targetManual: ManualId | "OTHER";
  targetLabel: string;
  targetNodeId: string | null;
  status: CrossRefStatus;
  resolvedBy?: "internal_pass" | "cross_manual_pass";
};

export type SearchHit = {
  node: StandardNode;
  similarity: number;
  matchedQuery: string;
};

export type SearchResult = {
  query: string;
  expansions: string[];
  hits: SearchHit[];
  threshold: number;
  /** True when nothing clears the threshold: the manuals do not cover the question. */
  notCovered: boolean;
};
