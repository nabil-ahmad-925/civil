"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { ManualTag } from "@/components/standards/ui";
import type { ChatReply, ChatSource, ChatTurn } from "@/lib/chat/types";

type Message = { role: "user" | "assistant"; text: string; reply?: ChatReply; error?: boolean };

const GREETING: Message = {
  role: "assistant",
  text: "Ask me about the manuals you have uploaded. I answer only from them and show the sections I used.",
};

const REL_LABEL: Record<ChatSource["relation"], string> = {
  match: "found for your question",
  mentions: "referenced from",
  mentioned_by: "mentions",
};

/** Turns "[S1][S3]" in the answer into small markers. */
function AnswerText({ text }: { text: string }) {
  const parts = text.split(/(\[S\d+\])/g);
  return (
    <p className="whitespace-pre-wrap">
      {parts.map((p, i) =>
        /^\[S\d+\]$/.test(p) ? (
          <sup key={i} className="mx-0.5 rounded bg-lime px-1 text-[10px] font-semibold text-principal-deep">
            {p.slice(1, -1)}
          </sup>
        ) : (
          <span key={i}>{p}</span>
        ),
      )}
    </p>
  );
}

function Sources({ sources }: { sources: ChatSource[] }) {
  if (sources.length === 0) return null;
  return (
    <div className="mt-3 border-t border-line pt-3">
      <p className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-muted">Sources</p>
      <ul className="space-y-2">
        {sources.map((s) => (
          <li key={s.ref} className="rounded-lg border border-line px-3 py-2 text-xs">
            <div className="flex flex-wrap items-center gap-2">
              <span className="rounded bg-lime px-1.5 py-0.5 font-semibold text-principal-deep">{s.ref}</span>
              <ManualTag id={s.manual} />
              <Link href={`/knowledge-base/browse?manual=${s.manual}&node=${s.id}`} className="font-medium text-principal hover:underline">
                {s.title}
              </Link>
              <span className="text-muted">pp. {s.pageStart}–{s.pageEnd}</span>
            </div>
            <p className="mt-1 text-muted">
              {REL_LABEL[s.relation]}
              {s.via ? ` ${s.via}` : ""}
              {s.mention ? ` (“${s.mention}”)` : ""}
            </p>
            {s.figures.length > 0 && (
              <div className="mt-2 flex flex-wrap gap-2">
                {s.figures.map((f) => (
                  <a key={f.id} href={f.url} target="_blank" rel="noreferrer" className="block w-40 rounded-md border border-line p-1 hover:bg-mint" title={f.caption}>
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img src={f.url} alt={f.caption || f.label} loading="lazy" className="h-auto w-full" />
                    <span className="mt-1 block truncate text-[11px] text-principal-deep">{f.label}</span>
                  </a>
                ))}
              </div>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}

export default function ChatPage() {
  const [messages, setMessages] = useState<Message[]>([GREETING]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, busy]);

  async function send(e: React.FormEvent) {
    e.preventDefault();
    const text = input.trim();
    if (!text || busy) return;
    const history: ChatTurn[] = messages
      .filter((m) => m !== GREETING && !m.error)
      .map((m) => ({ role: m.role, content: m.text }));
    setInput("");
    setBusy(true);
    setMessages((m) => [...m, { role: "user", text }]);
    try {
      const res = await fetch("/api/chat", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ message: text, history }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.error ?? "The chat service returned an error.");
      setMessages((m) => [...m, { role: "assistant", text: (data as ChatReply).answer, reply: data as ChatReply }]);
    } catch (err) {
      setMessages((m) => [...m, { role: "assistant", text: err instanceof Error ? err.message : "Something went wrong.", error: true }]);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex h-full flex-col">
      <header className="border-b border-line bg-blanco/80 px-8 py-5 backdrop-blur">
        <h1 className="text-xl font-bold text-principal-deep">Chat</h1>
      </header>

      <div className="flex-1 overflow-y-auto px-8 py-6">
        <div className="mx-auto flex max-w-3xl flex-col gap-4">
          {messages.map((m, i) => (
            <div key={i} className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}>
              <div
                className={`max-w-[85%] rounded-2xl px-4 py-3 text-sm leading-relaxed shadow-sm ${
                  m.role === "user"
                    ? "rounded-br-sm bg-principal text-blanco"
                    : m.error
                      ? "rounded-bl-sm border border-red-200 bg-red-50 text-red-800"
                      : "rounded-bl-sm border border-line bg-blanco text-texto"
                }`}
              >
                {m.reply ? <AnswerText text={m.text} /> : m.text}
                {m.reply && <Sources sources={m.reply.sources} />}
              </div>
            </div>
          ))}
          {busy && (
            <div className="flex justify-start">
              <div className="flex items-center gap-2 rounded-2xl rounded-bl-sm border border-line bg-blanco px-4 py-3 text-sm text-muted shadow-sm" role="status">
                <span className="h-4 w-4 animate-spin rounded-full border-2 border-mint border-t-principal" />
                Searching the manuals…
              </div>
            </div>
          )}
          <div ref={bottomRef} />
        </div>
      </div>

      <form onSubmit={send} className="border-t border-line bg-blanco/80 px-8 py-4 backdrop-blur">
        <div className="mx-auto flex max-w-3xl gap-3">
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="Ask about the manuals…"
            maxLength={2000}
            className="flex-1 rounded-xl border border-line bg-blanco px-4 py-3 text-sm outline-none transition placeholder:text-muted/60 focus:border-principal focus:ring-4 focus:ring-lime/70"
          />
          <button
            type="submit"
            className="rounded-xl bg-principal px-5 py-3 text-sm font-medium text-blanco shadow-md shadow-principal/25 transition hover:bg-principal-deep disabled:opacity-50"
            disabled={!input.trim() || busy}
          >
            Send
          </button>
        </div>
      </form>
    </div>
  );
}
