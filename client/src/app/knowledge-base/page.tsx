import Link from "next/link";
import DocumentsTable from "@/components/standards/DocumentsTable";
import InstructionsSection from "@/components/standards/InstructionsSection";
import { Card, PageHeader } from "@/components/standards/ui";
import { API_URL, ApiError, listDocuments } from "@/lib/documents/api";
import type { DocumentRecord } from "@/lib/documents/types";
import { listInstructions } from "@/lib/instructions/store";

export const dynamic = "force-dynamic";

type Loaded = { documents: DocumentRecord[] } | { failure: "unreachable" } | { failure: "server_error"; message: string };

async function loadDocuments(): Promise<Loaded> {
  try {
    return { documents: await listDocuments() };
  } catch (e) {
    return e instanceof ApiError ? { failure: "server_error", message: e.message } : { failure: "unreachable" };
  }
}

export default async function KnowledgeBasePage() {
  const [loaded, instructions] = await Promise.all([loadDocuments(), listInstructions()]);

  return (
    <div className="mx-auto max-w-6xl px-8 py-10">
      <PageHeader title="Knowledge Base" subtitle="The manuals you have uploaded and the instructions saved with them." sampleData={false}>
        <Link
          href="/knowledge-base/upload"
          className="rounded-xl bg-principal px-4 py-2.5 text-sm font-medium text-blanco shadow-md shadow-principal/25 transition hover:bg-principal-deep"
        >
          Upload a manual
        </Link>
      </PageHeader>

      <h2 className="mb-3 text-lg font-semibold text-principal-deep">Uploaded manuals</h2>
      {"documents" in loaded ? (
        <DocumentsTable initial={loaded.documents} />
      ) : loaded.failure === "server_error" ? (
        <Card className="border-amber-200 bg-amber-50/60 px-5 py-8 text-center">
          <p className="font-semibold text-amber-900">The server is running but returned an error</p>
          <p className="mx-auto mt-2 max-w-xl text-sm text-amber-800">{loaded.message}</p>
          <p className="mx-auto mt-2 max-w-xl text-xs text-amber-800">The server terminal has the details.</p>
        </Card>
      ) : (
        <Card className="border-amber-200 bg-amber-50/60 px-5 py-8 text-center">
          <p className="font-semibold text-amber-900">Cannot reach the server</p>
          <p className="mx-auto mt-2 max-w-xl text-sm text-amber-800">
            Nothing answered at <span className="font-mono">{API_URL}</span>. Start the backend, then reload:
          </p>
          <pre className="mx-auto mt-3 w-fit rounded-lg bg-blanco px-4 py-2 text-left font-mono text-xs text-texto">
            cd server && .venv/bin/uvicorn app.main:app --port 8000
          </pre>
        </Card>
      )}

      <InstructionsSection initial={instructions} />
    </div>
  );
}
