import { NextResponse } from "next/server";
import { createInstruction, InstructionError, listInstructions } from "@/lib/instructions/store";

export const runtime = "nodejs";

export async function GET() {
  return NextResponse.json(await listInstructions());
}

export async function POST(req: Request) {
  try {
    const body = await req.json().catch(() => ({}));
    return NextResponse.json(await createInstruction(body), { status: 201 });
  } catch (err) {
    if (err instanceof InstructionError) return NextResponse.json({ error: err.message }, { status: err.status });
    console.error(err);
    return NextResponse.json({ error: "Save failed." }, { status: 500 });
  }
}
