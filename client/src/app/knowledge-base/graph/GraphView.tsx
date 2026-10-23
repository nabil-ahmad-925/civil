"use client";

import { hierarchy, tree } from "d3-hierarchy";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Card, ManualTag, PageHeader } from "@/components/standards/ui";
import { API_URL, ApiError, listDocuments } from "@/lib/documents/api";
import { MANUALS, type Manual } from "@/lib/documents/types";
import { listCrossRefs, listFigures, listNodes } from "@/lib/nodes/api";
import type { CrossRefRow, Figure, NodeSummary } from "@/lib/nodes/types";

/** One box in the tree: the manual itself (top), a section, or a figure. */
type TNode = {
  id: string;
  kind: "manual" | "section" | "figure" | "ref";
  manual: Manual;
  title: string;
  section: string;
  pageStart: number;
  pageEnd: number;
  depth: number; // 0 for a chapter; -1 for the manual box
  parentId: string | null;
  children: TNode[];
  figure?: Figure;
  /** kind "ref": the section or figure this mention leads to (null when not linked), and how it stands. */
  refTarget?: string | null;
  refStatus?: string;
  refExternal?: boolean; // points at a document that is not one of the three manuals
};

type Data = { nodes: NodeSummary[]; figures: Figure[]; refs: CrossRefRow[] };

const CARD_W = 184;
const CARD_H = 48;
const GAP_X = 18;
const GAP_Y = 52;
const COLORS: Record<Manual, string> = { PPM: "#145b4b", HDM: "#b7791f", CADD: "#2b6cb0" };
const FIGURE_COLOR = "#d9822b";
const TITLE_CHARS = 27;

const manualId = (m: Manual) => `manual:${m}`;
const clip = (s: string, n: number) => (s.length > n ? `${s.slice(0, n - 1)}…` : s);

export default function GraphView() {
  const [data, setData] = useState<Data | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [manual, setManual] = useState<Manual>("PPM");
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [showFigures, setShowFigures] = useState(false);
  const [showRefs, setShowRefs] = useState(true);
  const [accordion, setAccordion] = useState(true);
  const [query, setQuery] = useState("");
  const [view, setView] = useState({ x: 0, y: 0, k: 1 });
  const [pending, setPending] = useState<string | null>(null); // a node to reveal once its manual is showing
  const [centerReq, setCenterReq] = useState<{ id: string; n: number } | null>(null);
  const [fitReq, setFitReq] = useState(0);

  const boxRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    (async () => {
      const docs = (await listDocuments()).filter((d) => d.status === "ingested");
      const [nodeLists, figureLists, refs] = await Promise.all([
        Promise.all(docs.map((d) => listNodes(d.id, ""))),
        Promise.all(docs.map((d) => listFigures(d.id))),
        listCrossRefs(),
      ]);
      const nodes = nodeLists.flat();
      setData({ nodes, figures: figureLists.flat(), refs });
      const present = MANUALS.map((m) => m.id).filter((id) => nodes.some((n) => n.manual === id));
      if (present.length && !present.includes("PPM")) setManual(present[0]);
    })().catch((e) =>
      setError(e instanceof ApiError ? e.message : `Cannot reach the server at ${API_URL}. Start the backend, then reload.`),
    );
  }, []);

  // --- the tree of the chosen manual --------------------------------------------------------------
  const model = useMemo(() => {
    const byId = new Map<string, TNode>();
    const root: TNode = {
      id: manualId(manual), kind: "manual", manual, title: MANUALS.find((m) => m.id === manual)?.label ?? manual,
      section: manual, pageStart: 0, pageEnd: 0, depth: -1, parentId: null, children: [],
    };
    byId.set(root.id, root);
    if (!data) return { root, byId };
    for (const n of data.nodes) {
      if (n.manual !== manual) continue;
      byId.set(n.id, {
        id: n.id, kind: "section", manual, title: n.title, section: n.section, pageStart: n.pageStart, pageEnd: n.pageEnd,
        depth: n.depth, parentId: n.parentId, children: [],
      });
    }
    for (const n of data.nodes) {
      if (n.manual !== manual) continue;
      const t = byId.get(n.id)!;
      const parent = (n.parentId && byId.get(n.parentId)) || root;
      t.parentId = parent.id;
      parent.children.push(t); // data comes in reading order
    }
    if (showFigures) {
      for (const f of data.figures) {
        const owner = f.manual === manual && f.nodeId ? byId.get(f.nodeId) : undefined;
        if (!owner) continue;
        const t: TNode = {
          id: f.id, kind: "figure", manual, title: f.caption || f.label, section: f.label, pageStart: f.page, pageEnd: f.page,
          depth: owner.depth + 1, parentId: owner.id, children: [], figure: f,
        };
        byId.set(f.id, t);
        owner.children.push(t);
      }
    }
    if (showRefs) {
      // Under each section: the places its text mentions, one box per target.
      const seen = new Set<string>();
      for (const r of data.refs) {
        const owner = r.source.manual === manual ? byId.get(r.source.id) : undefined;
        if (!owner) continue;
        const target = r.target?.id ?? r.targetFigure?.id ?? null;
        const key = `${owner.id}|${target ?? r.targetLabel}`;
        if (seen.has(key)) continue;
        seen.add(key);
        const known = MANUALS.some((m) => m.id === r.targetManual);
        const t: TNode = {
          id: `ref:${r.id}`, kind: "ref", manual: known ? (r.targetManual as Manual) : manual,
          title: r.target ? r.target.title : r.targetFigure ? `${r.targetFigure.label} ${r.targetFigure.caption}` : r.targetLabel,
          section: r.target?.section ?? r.targetFigure?.label ?? r.targetLabel, pageStart: 0, pageEnd: 0,
          depth: owner.depth + 1, parentId: owner.id, children: [], refTarget: target, refStatus: r.status, refExternal: !known,
        };
        byId.set(t.id, t);
        owner.children.push(t);
      }
    }
    return { root, byId };
  }, [data, manual, showFigures, showRefs]);

  const maxDepth = useMemo(() => Math.max(0, ...[...model.byId.values()].filter((n) => n.kind === "section").map((n) => n.depth)), [model]);

  const ancestorsOf = useCallback(
    (id: string): TNode[] => {
      const out: TNode[] = [];
      let cur = model.byId.get(id);
      while (cur?.parentId) {
        const p = model.byId.get(cur.parentId);
        if (!p) break;
        out.unshift(p);
        cur = p;
      }
      return out;
    },
    [model],
  );

  // --- layout: parents on top, children below ------------------------------------------------------
  const layout = useMemo(() => {
    const h = hierarchy<TNode>(model.root, (n) => (expanded.has(n.id) ? n.children : []));
    tree<TNode>().nodeSize([CARD_W + GAP_X, CARD_H + GAP_Y]).separation(() => 1)(h);
    const nodes = h.descendants().map((d) => ({ node: d.data, x: d.x ?? 0, y: d.y ?? 0, parent: d.parent?.data.id ?? null }));
    return { nodes, pos: new Map(nodes.map((n) => [n.node.id, n])) };
  }, [model, expanded]);

  // The path from the top to the selected box, and its direct children.
  const selected = selectedId ? model.byId.get(selectedId) ?? null : null;
  const pathIds = useMemo(() => new Set(selected ? [...ancestorsOf(selected.id).map((a) => a.id), selected.id] : []), [selected, ancestorsOf]);
  const childIds = useMemo(() => new Set(selected?.children.map((c) => c.id) ?? []), [selected]);

  // --- view: fit, centre -------------------------------------------------------------------------
  const fit = useCallback(() => {
    const box = boxRef.current;
    if (!box || layout.nodes.length === 0) return;
    const w = box.clientWidth, hgt = box.clientHeight;
    const xs = layout.nodes.map((n) => n.x), ys = layout.nodes.map((n) => n.y);
    const x0 = Math.min(...xs) - CARD_W / 2 - 30, x1 = Math.max(...xs) + CARD_W / 2 + 30;
    const y0 = Math.min(...ys) - CARD_H / 2 - 30, y1 = Math.max(...ys) + CARD_H / 2 + 30;
    const k = Math.min(1, w / (x1 - x0), hgt / (y1 - y0));
    setView({ k, x: w / 2 - ((x0 + x1) / 2) * k, y: Math.max(20, (hgt - (y1 - y0) * k) / 2) - y0 * k });
  }, [layout]);

  useEffect(() => {
    if (fitReq > 0) fit();
    // only when asked for (a new manual, "Fit"), not on every expand
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fitReq]);

  useEffect(() => {
    if (!centerReq) return;
    const p = layout.pos.get(centerReq.id);
    const box = boxRef.current;
    if (!p || !box) return;
    setView((v) => ({ ...v, x: box.clientWidth / 2 - p.x * v.k, y: box.clientHeight / 3 - p.y * v.k }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [centerReq]);

  // First picture of a manual: fit it.
  useEffect(() => {
    if (data) setFitReq((n) => n + 1);
  }, [data, manual]);

  // --- expanding, collapsing, revealing ---------------------------------------------------------
  const descendantsOf = useCallback(
    (id: string): string[] => {
      const out: string[] = [];
      const walk = (n: TNode) => n.children.forEach((c) => (out.push(c.id), walk(c)));
      const n = model.byId.get(id);
      if (n) walk(n);
      return out;
    },
    [model],
  );

  const toggle = (id: string) => {
    const n = model.byId.get(id);
    if (!n || n.children.length === 0) return;
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
        descendantsOf(id).forEach((d) => next.delete(d));
      } else {
        if (accordion && n.parentId) {
          for (const sib of model.byId.get(n.parentId)?.children ?? []) {
            if (sib.id !== id) {
              next.delete(sib.id);
              descendantsOf(sib.id).forEach((d) => next.delete(d));
            }
          }
        }
        next.add(id);
      }
      return next;
    });
  };

  const reveal = useCallback(
    (id: string) => {
      const target = model.byId.get(id);
      if (!target) {
        const owner = data?.nodes.find((n) => n.id === id)?.manual ?? data?.figures.find((f) => f.id === id)?.manual;
        if (owner && owner !== manual) {
          setPending(id);
          setManual(owner);
          setExpanded(new Set([manualId(owner)]));
        }
        return;
      }
      const path = ancestorsOf(id).map((a) => a.id);
      setExpanded((prev) => new Set([...(accordion ? [] : prev), manualId(manual), ...path]));
      setSelectedId(id);
      setCenterReq((c) => ({ id, n: (c?.n ?? 0) + 1 }));
    },
    [model, data, manual, accordion, ancestorsOf],
  );

  useEffect(() => {
    if (pending && model.byId.has(pending)) {
      reveal(pending);
      setPending(null);
    }
  }, [pending, model, reveal]);

  // The manual box starts open so its chapters show.
  useEffect(() => {
    setExpanded((prev) => (prev.has(manualId(manual)) ? prev : new Set([...prev, manualId(manual)])));
  }, [manual]);

  const expandToLevel = (level: number) => {
    const next = new Set<string>([manualId(manual)]);
    for (const n of model.byId.values()) if (n.kind === "section" && n.depth < level) next.add(n.id);
    setExpanded(next);
    setFitReq((n) => n + 1);
  };
  const collapseAll = () => {
    setExpanded(new Set([manualId(manual)]));
    setSelectedId(null);
    setFitReq((n) => n + 1);
  };

  // --- pan and zoom -----------------------------------------------------------------------------
  const drag = useRef<{ sx: number; sy: number; moved: boolean } | null>(null);
  const onPointerDown = (e: React.PointerEvent<SVGSVGElement>) => {
    if ((e.target as Element).closest("[data-card]")) return;
    e.currentTarget.setPointerCapture(e.pointerId);
    drag.current = { sx: e.clientX, sy: e.clientY, moved: false };
  };
  const onPointerMove = (e: React.PointerEvent<SVGSVGElement>) => {
    const d = drag.current;
    if (!d) return;
    const dx = e.clientX - d.sx, dy = e.clientY - d.sy;
    if (!d.moved && Math.hypot(dx, dy) < 3) return;
    d.moved = true;
    d.sx = e.clientX;
    d.sy = e.clientY;
    setView((v) => ({ ...v, x: v.x + dx, y: v.y + dy }));
  };
  const onPointerUp = () => {
    if (drag.current && !drag.current.moved) setSelectedId(null);
    drag.current = null;
  };
  const zoomAt = (px: number, py: number, factor: number) =>
    setView((v) => {
      const k = Math.min(3, Math.max(0.05, v.k * factor));
      return { k, x: px - ((px - v.x) / v.k) * k, y: py - ((py - v.y) / v.k) * k };
    });
  const onWheel = (e: React.WheelEvent) => {
    const r = boxRef.current!.getBoundingClientRect();
    zoomAt(e.clientX - r.left, e.clientY - r.top, Math.exp(-e.deltaY * 0.0015));
  };
  const zoomCentre = (f: number) => {
    const b = boxRef.current!;
    zoomAt(b.clientWidth / 2, b.clientHeight / 2, f);
  };

  // --- references of the selected box -----------------------------------------------------------
  const refsOut = useMemo(() => (selected && data ? data.refs.filter((r) => r.source.id === selected.id) : []), [selected, data]);
  const refsIn = useMemo(() => (selected && data ? data.refs.filter((r) => r.target?.id === selected.id || r.targetFigure?.id === selected.id) : []), [selected, data]);
  const arcs = useMemo(() => {
    const out: { id: string; from: string; to: string; kind: "out" | "in" }[] = [];
    for (const r of refsOut) {
      const to = r.target?.id ?? r.targetFigure?.id;
      if (!showRefs && to && selected && layout.pos.has(to)) out.push({ id: `o${r.id}`, from: selected.id, to, kind: "out" });
    }
    for (const r of refsIn) if (selected && layout.pos.has(r.source.id)) out.push({ id: `i${r.id}`, from: r.source.id, to: selected.id, kind: "in" });
    return out;
  }, [refsOut, refsIn, selected, layout, showRefs]);

  // --- search -------------------------------------------------------------------------------------
  const results = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q || !data) return [];
    return data.nodes.filter((n) => `${n.section} ${n.title}`.toLowerCase().includes(q)).slice(0, 8);
  }, [query, data]);

  const manualCounts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const n of data?.nodes ?? []) c[n.manual] = (c[n.manual] ?? 0) + 1;
    return c;
  }, [data]);

  const chip = (on: boolean) =>
    `rounded-full px-3 py-1 text-xs font-medium transition ${on ? "bg-principal-deep text-lime" : "bg-mint text-principal-deep hover:bg-lime"}`;
  const btn = "rounded-lg border border-line bg-blanco px-2.5 py-1 text-xs text-principal-deep shadow-sm hover:bg-mint";

  const linkPath = (a: { x: number; y: number }, b: { x: number; y: number }) => {
    const y1 = a.y + CARD_H / 2, y2 = b.y - CARD_H / 2, m = (y1 + y2) / 2;
    return `M${a.x},${y1} C${a.x},${m} ${b.x},${m} ${b.x},${y2}`;
  };
  const arcPath = (a: { x: number; y: number }, b: { x: number; y: number }) => {
    const mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2;
    const dx = b.x - a.x, dy = b.y - a.y;
    const len = Math.hypot(dx, dy) || 1;
    const bend = Math.min(160, len * 0.3);
    return `M${a.x},${a.y} Q${mx - (dy / len) * bend},${my + (dx / len) * bend} ${b.x},${b.y}`;
  };

  return (
    <div className="flex h-full flex-col px-8 py-8">
      <PageHeader
        title="Graph"
        subtitle="The manual as a tree, top to bottom. Click a box to see its parents and children; click the badge under a box to open or close what is inside it."
        sampleData={false}
      />

      {error ? (
        <Card className="border-red-200">
          <p className="text-sm text-red-700">{error}</p>
        </Card>
      ) : !data ? (
        <p className="text-sm text-muted">Loading the graph…</p>
      ) : (
        <>
          <div className="mb-3 flex flex-wrap items-center gap-2">
            {MANUALS.map((m) => (
              <button
                key={m.id}
                disabled={!manualCounts[m.id]}
                onClick={() => {
                  setManual(m.id);
                  setSelectedId(null);
                  setExpanded(new Set([manualId(m.id)]));
                }}
                className={`${chip(manual === m.id)} disabled:opacity-40`}
              >
                <span className="mr-1.5 inline-block h-2 w-2 rounded-full align-middle" style={{ background: COLORS[m.id] }} />
                {m.id} ({manualCounts[m.id] ?? 0})
              </button>
            ))}
            <span className="mx-1 h-4 w-px bg-line" />
            <button onClick={collapseAll} className={btn}>Collapse all</button>
            {Array.from({ length: Math.min(4, maxDepth + 1) }, (_, i) => i + 1).map((l) => (
              <button key={l} onClick={() => expandToLevel(l)} className={btn}>Open {l} level{l > 1 ? "s" : ""}</button>
            ))}
            <label className="ml-1 flex items-center gap-1.5 text-xs text-muted">
              <input type="checkbox" checked={accordion} onChange={(e) => setAccordion(e.target.checked)} />
              One branch open per level
            </label>
            <label className="flex items-center gap-1.5 text-xs text-muted">
              <input type="checkbox" checked={showFigures} onChange={(e) => setShowFigures(e.target.checked)} />
              Figures
            </label>
            <label className="flex items-center gap-1.5 text-xs text-muted">
              <input type="checkbox" checked={showRefs} onChange={(e) => setShowRefs(e.target.checked)} />
              Cross-references under sections
            </label>
            <div className="relative ml-auto">
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Find a section…"
                className="w-64 rounded-lg border border-line bg-white px-3 py-1.5 text-sm outline-none focus:border-principal"
              />
              {results.length > 0 && (
                <ul className="absolute right-0 z-20 mt-1 max-h-72 w-96 overflow-auto rounded-xl border border-line bg-blanco py-1 text-sm shadow-lg">
                  {results.map((n) => (
                    <li key={n.id}>
                      <button
                        onClick={() => {
                          reveal(n.id);
                          setQuery("");
                        }}
                        className="flex w-full items-center gap-2 px-3 py-1.5 text-left hover:bg-mint"
                      >
                        <ManualTag id={n.manual} />
                        <span className="truncate">{n.title}</span>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </div>

          <div className="flex min-h-0 flex-1 gap-4">
            <div ref={boxRef} className="relative min-h-[460px] flex-1 overflow-hidden rounded-2xl border border-line bg-blanco">
              <svg
                className="h-full w-full touch-none select-none"
                style={{ cursor: drag.current?.moved ? "grabbing" : "grab" }}
                onPointerDown={onPointerDown}
                onPointerMove={onPointerMove}
                onPointerUp={onPointerUp}
                onWheel={onWheel}
              >
                <defs>
                  <marker id="arrow-out" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto">
                    <path d="M0,0 L10,5 L0,10 z" fill="#2f9e6e" />
                  </marker>
                  <marker id="arrow-in" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto">
                    <path d="M0,0 L10,5 L0,10 z" fill="#2b6cb0" />
                  </marker>
                </defs>
                <g transform={`translate(${view.x},${view.y}) scale(${view.k})`}>
                  {/* parent -> child lines */}
                  {layout.nodes.map((n) => {
                    const p = n.parent ? layout.pos.get(n.parent) : null;
                    if (!p) return null;
                    const onPath = pathIds.has(n.node.id) && pathIds.has(p.node.id);
                    const toChild = selected && p.node.id === selected.id;
                    return (
                      <path
                        key={`l${n.node.id}`}
                        d={linkPath(p, n)}
                        fill="none"
                        stroke={onPath || toChild ? "#145b4b" : "#c5d0c4"}
                        strokeWidth={onPath || toChild ? 2.2 : 1.4}
                        opacity={selected && !onPath && !toChild ? 0.45 : 1}
                      />
                    );
                  })}

                  {/* cross-references of the selected box */}
                  {arcs.map((a) => {
                    const from = layout.pos.get(a.from)!, to = layout.pos.get(a.to)!;
                    return (
                      <path
                        key={a.id}
                        d={arcPath(from, to)}
                        fill="none"
                        stroke={a.kind === "out" ? "#2f9e6e" : "#2b6cb0"}
                        strokeWidth={1.8}
                        strokeDasharray="6 4"
                        markerEnd={`url(#arrow-${a.kind})`}
                      />
                    );
                  })}

                  {layout.nodes.map(({ node: n, x, y }) => {
                    const isSel = n.id === selectedId;
                    const emphasis = isSel || pathIds.has(n.id) || childIds.has(n.id);
                    const color = n.kind === "figure" ? FIGURE_COLOR : n.refExternal ? "#7a8580" : COLORS[n.manual];
                    const isRef = n.kind === "ref";
                    const isOpen = expanded.has(n.id);
                    return (
                      <g key={n.id} transform={`translate(${x - CARD_W / 2},${y - CARD_H / 2})`} opacity={selected && !emphasis ? 0.5 : 1}>
                        <g
                          data-card
                          onClick={() => (isRef && n.refTarget ? reveal(n.refTarget) : setSelectedId(n.id))}
                          style={{ cursor: "pointer" }}
                        >
                          <title>{n.title}</title>
                          <rect
                            width={CARD_W}
                            height={CARD_H}
                            rx={10}
                            fill={n.kind === "manual" ? color : isSel ? "#e9f1e9" : isRef ? "#f4faf6" : "#ffffff"}
                            stroke={color}
                            strokeWidth={isSel ? 3 : 1.5}
                            strokeDasharray={isRef ? "5 3" : undefined}
                          />
                          <rect width={5} height={CARD_H} rx={2.5} fill={color} />
                          <text x={14} y={20} fontSize={12} fontWeight={600} fill={n.kind === "manual" ? "#ffffff" : "#103f36"}>
                            {n.kind === "figure" ? "▣ " : isRef ? "↪ " : ""}{clip(n.title, TITLE_CHARS)}
                          </text>
                          <text x={14} y={37} fontSize={10.5} fill={n.kind === "manual" ? "#d6e9ad" : "#5b6c67"}>
                            {n.kind === "manual" ? `${manualCounts[manual] ?? 0} sections` : n.kind === "figure" ? `${n.section} · p. ${n.pageStart}` : isRef ? (n.refTarget ? `${n.manual} · § ${n.section}` : `${n.section} · ${n.refStatus?.replace(/_/g, " ")}`) : `§ ${n.section} · p. ${n.pageStart}`}
                          </text>
                        </g>
                        {n.children.length > 0 && (
                          <g
                            data-card
                            onClick={() => toggle(n.id)}
                            style={{ cursor: "pointer" }}
                            transform={`translate(${CARD_W / 2},${CARD_H + 1})`}
                          >
                            <circle r={11} fill={isOpen ? "#ffffff" : color} stroke={color} strokeWidth={1.5} />
                            <text textAnchor="middle" y={4} fontSize={10.5} fontWeight={700} fill={isOpen ? color : "#ffffff"}>
                              {isOpen ? "−" : n.children.length}
                            </text>
                          </g>
                        )}
                      </g>
                    );
                  })}
                </g>
              </svg>

              <div className="absolute right-3 top-3 flex flex-col gap-1">
                <button onClick={() => zoomCentre(1.3)} className={btn} aria-label="Zoom in">+</button>
                <button onClick={() => zoomCentre(1 / 1.3)} className={btn} aria-label="Zoom out">−</button>
                <button onClick={() => setFitReq((n) => n + 1)} className={btn} aria-label="Fit to screen">Fit</button>
              </div>
              <p className="pointer-events-none absolute bottom-3 left-3 text-[11px] text-muted">
                {layout.nodes.length - 1} boxes showing · scroll to zoom · drag to move
              </p>
            </div>

          </div>
        </>
      )}
    </div>
  );
}
