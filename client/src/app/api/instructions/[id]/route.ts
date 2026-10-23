import { NextResponse } from "next/server";
import { deleteInstruction, InstructionError, updateInstruction } from "@/lib/instructions/store";

export const runtime = "nodejs";

type Ctx = { params: Promise<{ id: string }> };

function fail(err: unknown) {
  if (err instanceof InstructionError) return NextResponse.json({ error: err.message }, { status: err.status });
  console.error(err);
  return NextResponse.json({ error: "Request failed." }, { status: 500 });
}

export async function PUT(req: Request, { params }: Ctx) {
  try {
    const { id } = await params;
    const body = await req.json().catch(() => ({}));
    return NextResponse.json(await updateInstruction(id, body));
  } catch (err) {
    return fail(err);
  }
}

export async function DELETE(_req: Request, { params }: Ctx) {
  try {
    const { id } = await params;
    await deleteInstruction(id);
    return new NextResponse(null, { status: 204 });
  } catch (err) {
    return fail(err);
  }
}
