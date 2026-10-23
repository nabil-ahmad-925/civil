// Sample data standing in for the ingestion API. Section numbers follow the real
// manuals' structure, but the text is illustrative and must not be cited.
import type { CrossRef, Manual, ManualId, StandardNode } from "./types";

export const manuals: Manual[] = [
  {
    id: "PPM",
    title: "PPM",
    fullTitle: "Plans Preparation Manual",
    version: "2024-07",
    sourceHash: "a91f3c0e",
    chapters: [
      { id: "ppm-2", number: "2", title: "Project Plans" },
      { id: "ppm-ex", number: "Ex", title: "Example Plan Sheets" },
    ],
    pipeline: {
      chunking: { status: "done", done: 412, total: 412 },
      internalRefs: { status: "done", done: 188, total: 188 },
      vectors: { status: "done", done: 412, total: 412 },
    },
  },
  {
    id: "HDM",
    title: "HDM",
    fullTitle: "Highway Design Manual",
    version: "7th ed. 2023",
    sourceHash: "5d20be77",
    chapters: [
      { id: "hdm-200", number: "200", title: "Geometric Design and Structure Standards" },
      { id: "hdm-300", number: "300", title: "Geometric Cross Section" },
      { id: "hdm-1100", number: "1100", title: "Highway Traffic Noise Abatement" },
    ],
    pipeline: {
      chunking: { status: "done", done: 1630, total: 1630 },
      internalRefs: { status: "done", done: 702, total: 702 },
      vectors: { status: "done", done: 1630, total: 1630 },
    },
  },
  {
    id: "CADD",
    title: "CADD",
    fullTitle: "CADD Users Manual",
    version: "2022-11",
    sourceHash: "c4e81a09",
    chapters: [{ id: "cadd-2", number: "2", title: "Drafting Standards" }],
    pipeline: {
      chunking: { status: "running", done: 214, total: 340 },
      internalRefs: { status: "pending", done: 0, total: 0, note: "Starts when chunking finishes" },
      vectors: { status: "running", done: 214, total: 340 },
    },
  },
];

/** Final pass across all manuals; only possible once every manual is chunked. */
export const crossManualPass = {
  status: "blocked" as const,
  note: "Waiting for CADD chunking to finish",
};

export const taggingPass = {
  status: "running" as const,
  done: 1288,
  total: 2382,
};

export const nodes: StandardNode[] = [
  {
    id: "ppm-2-2",
    manual: "PPM",
    chapterId: "ppm-2",
    section: "2.2",
    title: "Layout Sheets",
    page: 34,
    text:
      "Layout sheets show the horizontal alignment, stationing, edges of traveled way, lane and shoulder lines, and the limits of work. Layout sheets are prepared at a scale appropriate to the project and numbered consecutively with the prefix L.",
    embedded: true,
    classification: "guidance",
    tags: ["layout", "sheet-type", "drafting"],
    applicability: [{ id: "a1", input: "sheet_type", text: "Applies to sheets of type Layout (L)." }],
    exceptions: [],
  },
  {
    id: "ppm-2-2-1",
    manual: "PPM",
    chapterId: "ppm-2",
    section: "2.2.1",
    title: "Required Items on Layout Sheets",
    page: 35,
    text:
      "Each layout sheet shall include a north arrow, a scale bar, the alignment designation with stationing, a legend of symbols used, and the title block described in Section 2.1.3. Line weights and text sizes shall conform to Section 2.6 of the CADD Users Manual.",
    embedded: true,
    classification: "requirement",
    tags: ["layout", "required-items", "drafting"],
    applicability: [{ id: "a1", input: "sheet_type", text: "Applies to sheets of type Layout (L)." }],
    exceptions: [],
  },
  {
    id: "ppm-2-1-3",
    manual: "PPM",
    chapterId: "ppm-2",
    section: "2.1.3",
    title: "Title Block",
    page: 22,
    text:
      "The title block shall show the district, county, route, post miles, sheet number, total sheets, and the registered engineer's signature and seal.",
    embedded: true,
    classification: "requirement",
    tags: ["title-block", "signature", "drafting"],
    applicability: [{ id: "a1", input: "sheet_type", text: "Applies to all project plan sheets." }],
    exceptions: [],
  },
  {
    id: "ppm-ex-65",
    manual: "PPM",
    chapterId: "ppm-ex",
    section: "Example 65",
    title: "Layout-7 (Sheet L-7)",
    page: 288,
    text:
      "Example layout sheet illustrating alignment, stationing, edge of traveled way, sound wall location, and required layout items for a multilane highway segment.",
    embedded: true,
    classification: "non_governing",
    tags: ["layout", "example", "golden-example"],
    applicability: [],
    exceptions: [],
  },
  {
    id: "hdm-301-1",
    manual: "HDM",
    chapterId: "hdm-300",
    section: "301.1",
    title: "Lane Width",
    page: 300,
    text:
      "The minimum lane width on two-lane and multilane highways, ramps, collector-distributor roads, and other appurtenant roadways shall be 12 feet, except as provided below.",
    embedded: true,
    classification: "requirement",
    requirementClass: "boldface",
    tags: ["lane-width", "cross-section", "engineering"],
    applicability: [
      { id: "a1", input: "facility_type", text: "Two-lane and multilane highways, ramps, and collector-distributor roads." },
    ],
    exceptions: [
      {
        id: "e1",
        text: "On conventional highways with low posted speeds and low truck volumes, a reduced lane width may be used.",
        requiredInputs: ["posted_speed_mph", "truck_volume_aadtt"],
      },
    ],
  },
  {
    id: "hdm-302-1",
    manual: "HDM",
    chapterId: "hdm-300",
    section: "302.1",
    title: "Shoulder Width",
    page: 302,
    text:
      "Shoulder widths shall be as shown in the shoulder width table for the facility type. See Index 301.1 for lane width requirements on the same facility.",
    embedded: true,
    classification: "requirement",
    requirementClass: "underlined",
    tags: ["shoulder", "cross-section", "engineering"],
    applicability: [{ id: "a1", input: "facility_type", text: "Varies by facility type per table." }],
    exceptions: [],
  },
  {
    id: "hdm-208",
    manual: "HDM",
    chapterId: "hdm-200",
    section: "208",
    title: "Bridges and Grade Separation Structures",
    page: 210,
    text:
      "Grade separation structures, including overcrossings, undercrossings, and separation ramps, shall be designed to provide the clearances in Index 309 and the cross-section widths of the approach roadway.",
    embedded: true,
    classification: "requirement",
    requirementClass: "boldface",
    tags: ["grade-separation", "structures", "overcrossing", "engineering"],
    applicability: [{ id: "a1", input: "structure_type", text: "Applies to bridges and grade separation structures." }],
    exceptions: [],
  },
  {
    id: "hdm-309-2",
    manual: "HDM",
    chapterId: "hdm-300",
    section: "309.2",
    title: "Vertical Clearances",
    page: 330,
    text:
      "The minimum vertical clearance over the traveled way and shoulders of freeways and expressways at overcrossing structures shall be as shown in the vertical clearance table.",
    embedded: true,
    classification: "requirement",
    requirementClass: "boldface",
    tags: ["vertical-clearance", "overcrossing", "structures", "engineering"],
    applicability: [{ id: "a1", input: "facility_type", text: "Freeways and expressways at overcrossings." }],
    exceptions: [
      {
        id: "e1",
        text: "Reduced clearance may be approved where reconstruction of an existing structure is not justified.",
        requiredInputs: ["is_existing_structure", "approval_on_file"],
      },
    ],
  },
  {
    id: "hdm-1102",
    manual: "HDM",
    chapterId: "hdm-1100",
    section: "1102.2",
    title: "Sound Wall Location",
    page: 1105,
    text:
      "Sound walls should be located to provide the clear recovery zone from the edge of traveled way. Where this is not practical, a barrier shall shield the wall.",
    embedded: true,
    classification: null,
    tags: [],
    applicability: [],
    exceptions: [],
  },
  {
    id: "cadd-2-6",
    manual: "CADD",
    chapterId: "cadd-2",
    section: "2.6",
    title: "Line Weights and Text Sizes",
    page: 48,
    text:
      "Line weights and text heights for plan sheets are assigned by level according to the standard level table. Text on plotted full-size sheets shall be no smaller than the minimum height listed in the table.",
    embedded: true,
    classification: null,
    tags: [],
    applicability: [],
    exceptions: [],
  },
  {
    id: "cadd-2-4",
    manual: "CADD",
    chapterId: "cadd-2",
    section: "2.4",
    title: "Level Names",
    page: 41,
    text:
      "Graphic elements shall be placed on the standard level for their feature type. Elements on non-standard levels are not permitted in final plans.",
    embedded: false,
    classification: null,
    tags: [],
    applicability: [],
    exceptions: [],
  },
];

export const crossRefs: CrossRef[] = [
  {
    id: "x1",
    fromNodeId: "ppm-2-2-1",
    mention: "the title block described in Section 2.1.3",
    targetManual: "PPM",
    targetLabel: "PPM 2.1.3",
    targetNodeId: "ppm-2-1-3",
    status: "resolved",
    resolvedBy: "internal_pass",
  },
  {
    id: "x2",
    fromNodeId: "ppm-2-2-1",
    mention: "Section 2.6 of the CADD Users Manual",
    targetManual: "CADD",
    targetLabel: "CADD 2.6",
    targetNodeId: null,
    status: "awaiting_cross",
  },
  {
    id: "x3",
    fromNodeId: "hdm-302-1",
    mention: "See Index 301.1",
    targetManual: "HDM",
    targetLabel: "HDM 301.1",
    targetNodeId: "hdm-301-1",
    status: "resolved",
    resolvedBy: "internal_pass",
  },
  {
    id: "x4",
    fromNodeId: "hdm-208",
    mention: "the clearances in Index 309",
    targetManual: "HDM",
    targetLabel: "HDM 309",
    targetNodeId: "hdm-309-2",
    status: "resolved",
    resolvedBy: "internal_pass",
  },
  {
    id: "x5",
    fromNodeId: "hdm-309-2",
    mention: "the vertical clearance table",
    targetManual: "HDM",
    targetLabel: "HDM Table 309.2A",
    targetNodeId: null,
    status: "unresolved",
  },
  {
    id: "x6",
    fromNodeId: "cadd-2-6",
    mention: "the standard level table",
    targetManual: "CADD",
    targetLabel: "CADD Table 2.4A",
    targetNodeId: null,
    status: "unresolved",
  },
];

export function getManual(id: ManualId) {
  return manuals.find((m) => m.id === id)!;
}

export function getNode(id: string) {
  return nodes.find((n) => n.id === id);
}

export function refsFrom(nodeId: string) {
  return crossRefs.filter((r) => r.fromNodeId === nodeId);
}

export function refsTo(nodeId: string) {
  return crossRefs.filter((r) => r.targetNodeId === nodeId);
}
