import "server-only";
import { randomUUID } from "node:crypto";
import { mkdir, readFile, rename, writeFile } from "node:fs/promises";
import path from "node:path";
import { MAX_INSTRUCTION_CHARS, MAX_TITLE_CHARS, type InstructionRecord } from "./types";

const DIR = path.join(process.cwd(), "storage");
const FILE = path.join(DIR, "instructions.json");

export class InstructionError extends Error {
  constructor(
    message: string,
    public status = 400,
  ) {
    super(message);
  }
}

// Serialise writes so concurrent saves don't clobber each other.
let queue: Promise<unknown> = Promise.resolve();
function withLock<T>(fn: () => Promise<T>): Promise<T> {
  const run = queue.then(fn, fn);
  queue = run.catch(() => {});
  return run;
}

async function readAll(): Promise<InstructionRecord[]> {
  try {
    return JSON.parse(await readFile(FILE, "utf8"));
  } catch {
    return [];
  }
}

async function writeAll(records: InstructionRecord[]) {
  await mkdir(DIR, { recursive: true });
  const tmp = `${FILE}.tmp`;
  await writeFile(tmp, JSON.stringify(records, null, 2));
  await rename(tmp, FILE);
}

function clean(input: { title?: unknown; text?: unknown }) {
  const text = typeof input.text === "string" ? input.text.trim() : "";
  const title = typeof input.title === "string" ? input.title.trim().slice(0, MAX_TITLE_CHARS) : "";
  if (!text) throw new InstructionError("Write some instructions before saving.");
  if (text.length > MAX_INSTRUCTION_CHARS) {
    throw new InstructionError(`Instructions are limited to ${MAX_INSTRUCTION_CHARS.toLocaleString()} characters.`, 413);
  }
  return { title, text };
}

export async function listInstructions() {
  const records = await readAll();
  return records.sort((a, b) => b.updatedAt.localeCompare(a.updatedAt));
}

export function createInstruction(input: { title?: unknown; text?: unknown }) {
  const { title, text } = clean(input);
  return withLock(async () => {
    const now = new Date().toISOString();
    const record: InstructionRecord = { id: randomUUID(), title, text, createdAt: now, updatedAt: now };
    await writeAll([...(await readAll()), record]);
    return record;
  });
}

export function updateInstruction(id: string, input: { title?: unknown; text?: unknown }) {
  const { title, text } = clean(input);
  return withLock(async () => {
    const records = await readAll();
    const existing = records.find((r) => r.id === id);
    if (!existing) throw new InstructionError("Instruction not found.", 404);
    const updated = { ...existing, title, text, updatedAt: new Date().toISOString() };
    await writeAll(records.map((r) => (r.id === id ? updated : r)));
    return updated;
  });
}

export function deleteInstruction(id: string) {
  return withLock(async () => {
    const records = await readAll();
    if (!records.some((r) => r.id === id)) throw new InstructionError("Instruction not found.", 404);
    await writeAll(records.filter((r) => r.id !== id));
  });
}
