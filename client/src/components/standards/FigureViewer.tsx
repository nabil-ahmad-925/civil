"use client";

import { useEffect, useState } from "react";
import { figureImageUrl } from "@/lib/nodes/api";
import type { Figure } from "@/lib/nodes/types";
import { ManualTag } from "./ui";

/** A button that shows a figure's label and opens the figure (loaded from the bucket through the API) in a dialog. */
export function FigureLink({ figure }: { figure: Figure }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button onClick={() => setOpen(true)} className="text-left font-medium text-principal hover:underline">
        <span className="mr-1.5 inline-block align-middle">
          <ManualTag id={figure.manual} />
        </span>
        {figure.label} <span className="text-xs font-normal text-muted">(p. {figure.page})</span>
      </button>
      {open && <FigureDialog figure={figure} onClose={() => setOpen(false)} />}
    </>
  );
}

function FigureDialog({ figure, onClose }: { figure: Figure; onClose: () => void }) {
  const [failed, setFailed] = useState(false);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={figure.label}
      onClick={onClose}
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-6"
    >
      <div onClick={(e) => e.stopPropagation()} className="flex max-h-full w-full max-w-4xl flex-col rounded-xl bg-white shadow-xl">
        <div className="flex items-start justify-between gap-4 border-b border-line px-5 py-3">
          <div className="min-w-0">
            <p className="font-semibold text-principal-deep">{figure.label}</p>
            <p className="text-xs text-muted">{figure.caption}</p>
            <p className="text-xs text-muted">Page {figure.page}</p>
          </div>
          <button onClick={onClose} className="rounded-lg px-2 py-1 text-sm text-muted hover:bg-mint" aria-label="Close">
            ✕
          </button>
        </div>
        <div className="overflow-auto p-4">
          {failed ? (
            <p className="py-10 text-center text-sm text-red-700">The image could not be loaded.</p>
          ) : (
            <>
              {!loaded && (
                <div role="status" className="flex flex-col items-center gap-3 py-16 text-sm text-muted">
                  <span className="h-8 w-8 animate-spin rounded-full border-4 border-mint border-t-principal" />
                  Loading figure…
                </div>
              )}
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src={figureImageUrl(figure.id)}
                alt={figure.caption || figure.label}
                onLoad={() => setLoaded(true)}
                onError={() => setFailed(true)}
                className={`mx-auto h-auto max-w-full ${loaded ? "" : "hidden"}`}
              />
            </>
          )}
        </div>
      </div>
    </div>
  );
}
