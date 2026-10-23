import { NextResponse } from "next/server";

export const runtime = "nodejs";

/**
 * Forwards a chat message to the AWS chat API (infra/). Runs on the server so the API key never reaches the browser.
 * Set CHAT_API_URL and CHAT_API_KEY in client/.env.local (see infra/README.md).
 */
export async function POST(req: Request) {
  const url = process.env.CHAT_API_URL?.replace(/\/$/, "");
  const key = process.env.CHAT_API_KEY;
  if (!url || !key) {
    return NextResponse.json({ error: "Chat is not connected. Set CHAT_API_URL and CHAT_API_KEY in client/.env.local." }, { status: 503 });
  }
  const body = await req.json().catch(() => null);
  if (!body || typeof body.message !== "string") return NextResponse.json({ error: "message is required." }, { status: 400 });

  try {
    const res = await fetch(`${url}/chat`, {
      method: "POST",
      headers: { "content-type": "application/json", "x-api-key": key },
      body: JSON.stringify({ message: body.message, history: body.history ?? [], manual: body.manual ?? null }),
      cache: "no-store",
    });
    const data = await res.json().catch(() => ({ error: "The chat service sent an unreadable answer." }));
    // A wrong key is our configuration problem, not something the user can fix: do not pass the 401 through as is.
    if (res.status === 401) return NextResponse.json({ error: "The chat service refused the API key." }, { status: 502 });
    return NextResponse.json(data, { status: res.status });
  } catch {
    return NextResponse.json({ error: "Cannot reach the chat service." }, { status: 502 });
  }
}
