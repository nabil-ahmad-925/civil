"use client";

import { useState } from "react";
import { Card } from "@/components/standards/ui";
import { MAX_INSTRUCTION_CHARS, MAX_TITLE_CHARS, type InstructionRecord } from "@/lib/instructions/types";

function heading(r: InstructionRecord) {
  if (r.title) return r.title;
  const first = r.text.split("\n")[0];
  return first.length > 70 ? `${first.slice(0, 70)}…` : first;
}

export default function InstructionsSection({ initial }: { initial: InstructionRecord[] }) {
  const [items, setItems] = useState(initial);
  const [title, setTitle] = useState("");
  const [text, setText] = useState("");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function reset() {
    setTitle("");
    setText("");
    setEditingId(null);
    setError(null);
  }

  async function save() {
    if (!text.trim() || saving) return;
    setSaving(true);
    setError(null);
    try {
      const res = await fetch(editingId ? `/api/instructions/${editingId}` : "/api/instructions", {
        method: editingId ? "PUT" : "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title, text }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        setError(body.error ?? "Save failed.");
        return;
      }
      setItems((prev) => [body as InstructionRecord, ...prev.filter((r) => r.id !== body.id)]);
      reset();
    } catch {
      setError("Network error. Nothing was saved.");
    } finally {
      setSaving(false);
    }
  }

  async function remove(r: InstructionRecord) {
    if (!confirm(`Delete “${heading(r)}”?`)) return;
    const res = await fetch(`/api/instructions/${r.id}`, { method: "DELETE" });
    if (!res.ok && res.status !== 404) {
      const body = await res.json().catch(() => ({}));
      setError(body.error ?? "Delete failed.");
      return;
    }
    setItems((prev) => prev.filter((x) => x.id !== r.id));
    if (editingId === r.id) reset();
  }

  function edit(r: InstructionRecord) {
    setEditingId(r.id);
    setTitle(r.title);
    setText(r.text);
    setError(null);
    window.scrollTo({ top: document.getElementById("instructions")?.offsetTop ?? 0, behavior: "smooth" });
  }

  return (
    <section id="instructions" className="mt-12">
      <h2 className="text-lg font-semibold text-principal-deep">Instructions</h2>
      <p className="mb-4 mt-1 text-sm text-muted">
        Add any instructions or notes in your own words. Each one is saved to the Knowledge Base.
      </p>

      <Card className="p-5">
        {editingId && (
          <p className="mb-3 rounded-lg bg-lime/50 px-3 py-2 text-xs font-medium text-principal-deep">
            Editing a saved instruction
          </p>
        )}
        <input
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          maxLength={MAX_TITLE_CHARS}
          disabled={saving}
          placeholder="Title (optional)"
          className="mb-3 w-full rounded-xl border border-line bg-blanco px-4 py-2.5 text-sm outline-none transition placeholder:text-muted/60 focus:border-principal focus:ring-4 focus:ring-lime/70"
        />
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          maxLength={MAX_INSTRUCTION_CHARS}
          disabled={saving}
          rows={6}
          placeholder="Type instructions here, e.g. which sections take priority, terms to treat as equivalent, or rules the team should follow..."
          className="w-full resize-y rounded-xl border border-line bg-blanco px-4 py-3 text-sm leading-relaxed outline-none transition placeholder:text-muted/60 focus:border-principal focus:ring-4 focus:ring-lime/70"
        />
        <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
          <span className="text-xs text-muted">
            {text.length.toLocaleString()} / {MAX_INSTRUCTION_CHARS.toLocaleString()}
          </span>
          <div className="flex items-center gap-3">
            {(editingId || text || title) && (
              <button onClick={reset} disabled={saving} className="text-sm text-muted hover:text-principal-deep">
                {editingId ? "Cancel" : "Clear"}
              </button>
            )}
            <button
              onClick={save}
              disabled={!text.trim() || saving}
              className="rounded-xl bg-principal px-5 py-2.5 text-sm font-medium text-blanco shadow-md shadow-principal/25 transition hover:bg-principal-deep disabled:cursor-not-allowed disabled:opacity-50"
            >
              {saving ? "Saving…" : editingId ? "Save changes" : "Save to Knowledge Base"}
            </button>
          </div>
        </div>
        {error && (
          <p className="mt-3 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{error}</p>
        )}
      </Card>

      <div className="mt-5 space-y-3">
        {items.length === 0 ? (
          <p className="rounded-2xl border border-dashed border-line px-5 py-8 text-center text-sm text-muted">
            No instructions saved yet.
          </p>
        ) : (
          items.map((r) => (
            <Card key={r.id} className={editingId === r.id ? "border-principal ring-2 ring-lime" : ""}>
              <div className="flex items-start justify-between gap-4">
                <div className="min-w-0">
                  <h3 className="truncate font-semibold text-principal-deep">{heading(r)}</h3>
                  <p className="mt-0.5 text-xs text-muted">
                    Saved {new Date(r.createdAt).toLocaleString()}
                    {r.updatedAt !== r.createdAt && ` · edited ${new Date(r.updatedAt).toLocaleString()}`}
                  </p>
                </div>
                <div className="flex shrink-0 gap-3 text-sm">
                  <button onClick={() => edit(r)} className="font-medium text-principal hover:underline">
                    Edit
                  </button>
                  <button onClick={() => remove(r)} className="text-muted hover:text-red-700">
                    Delete
                  </button>
                </div>
              </div>
              <p className="mt-3 whitespace-pre-wrap text-sm leading-relaxed text-texto">{r.text}</p>
            </Card>
          ))
        )}
      </div>
    </section>
  );
}
