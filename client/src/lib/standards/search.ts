// Mock retrieval. Stands in for: LLM query rewriting -> embedding -> pgvector
// similarity over node embeddings. Keep the SearchResult shape when replacing it.
import { nodes } from "./data";
import type { SearchHit, SearchResult } from "./types";

export const SIMILARITY_THRESHOLD = 0.35;

// Plays the role of the per-query LLM rephrasing step (not stored).
const vocabulary: Record<string, string[]> = {
  flyover: ["grade separation structure", "overcrossing", "vertical clearance"],
  overpass: ["overcrossing", "grade separation structure"],
  bridge: ["grade separation structure", "structures"],
  lane: ["lane width"],
  shoulder: ["shoulder width"],
  font: ["text sizes", "text height"],
  text: ["text sizes", "text height"],
  "line weight": ["line weights", "level"],
  noise: ["sound wall"],
  "title block": ["title block", "signature"],
};

const stopwords = new Set(["how", "what", "the", "and", "for", "does", "should", "can", "with", "are", "from", "about"]);

function tokens(s: string) {
  return (s.toLowerCase().match(/[a-z0-9.]+/g) ?? []).filter((t) => !stopwords.has(t));
}

function expand(query: string) {
  const q = query.toLowerCase();
  const out = new Set<string>();
  for (const [term, alts] of Object.entries(vocabulary)) {
    if (q.includes(term)) alts.forEach((a) => out.add(a));
  }
  return [...out];
}

function score(query: string, haystack: string) {
  const qt = tokens(query).filter((t) => t.length > 2);
  if (qt.length === 0) return 0;
  const ht = new Set(tokens(haystack));
  // Allow plural/tense variants only ("weight" ~ "weights"), not loose prefixes.
  const near = (a: string, b: string) =>
    Math.min(a.length, b.length) > 3 && Math.abs(a.length - b.length) <= 2 && (a.startsWith(b) || b.startsWith(a));
  const hits = qt.filter((t) => ht.has(t) || [...ht].some((h) => near(h, t)));
  return hits.length / qt.length;
}

export function searchStandards(query: string): SearchResult {
  const expansions = expand(query);
  const queries = [query, ...expansions];
  const best = new Map<string, SearchHit>();

  for (const node of nodes) {
    if (!node.embedded) continue;
    const haystack = `${node.title} ${node.text} ${node.tags.join(" ")}`;
    for (const q of queries) {
      // Matches via a rephrasing count slightly less than matches on the user's own words.
      const s = Math.min(0.97, score(q, haystack) * (q === query ? 0.95 : 0.85));
      const prev = best.get(node.id);
      if (!prev || s > prev.similarity) best.set(node.id, { node, similarity: s, matchedQuery: q });
    }
  }

  const hits = [...best.values()]
    .filter((h) => h.similarity >= SIMILARITY_THRESHOLD)
    .sort((a, b) => b.similarity - a.similarity)
    .slice(0, 6);

  return { query, expansions, hits, threshold: SIMILARITY_THRESHOLD, notCovered: hits.length === 0 };
}
