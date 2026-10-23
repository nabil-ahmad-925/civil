# Server (FastAPI + Supabase)

Takes a manual PDF (PPM, HDM or CADD Users Manual), saves it to a Supabase bucket, then cuts it into
chunks (nodes) with cross-references and vectors and saves those in Postgres.
See [docs/INGESTION_FLOW.md](docs/INGESTION_FLOW.md).

## The flow

1. `POST /api/documents` saves the PDF to the bucket and records it (`status = uploaded`). Nothing is chunked yet.
2. `POST /api/documents/{id}/ingest` reads that PDF back from the bucket, chunks it, and saves the nodes,
   cross-references and vectors (`status = ingested`). Safe to run again: it replaces the document's chunks.

The client does both calls in a row when you press "Upload and ingest".

## Set up

```bash
cd server
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env        # then fill it in
```

**Supabase** (once):

1. Create a project. Run [sql/schema.sql](sql/schema.sql) in the SQL editor. It creates the tables, the pgvector
   extension and the private `manuals` storage bucket. (If you ran an older version of this file, the first lines
   explain how to reset.)
2. Put the project URL and the **service_role** key in `.env`. That key bypasses row security: keep it on the
   server, never in the client.
3. `EMBEDDING_DIM` must match `vector(1536)` in the schema. If your embedding model has another size, change both.

**Try it with no Supabase** (data is lost on restart): `REPOSITORY=memory`.

## Run

```bash
.venv/bin/uvicorn app.main:app --reload --port 8000
```

Interactive API docs: http://localhost:8000/docs

## Test

```bash
.venv/bin/python -m pytest
```

## Endpoints

| Method | Path | |
| --- | --- | --- |
| GET | `/health` | |
| GET | `/api/documents` | All uploaded files, with status and counts |
| POST | `/api/documents?manual=PPM&version=…&filename=…` | Raw PDF as the body. Saves to the bucket. `version` is optional |
| POST | `/api/documents/{id}/ingest` | Chunks the stored PDF and saves nodes, refs, vectors. 422 if the PDF has no usable headings |
| DELETE | `/api/documents/{id}` | Removes the file, its chunks and its cross-references |
| GET | `/api/nodes?document_id=…&q=…` | The document's nodes in reading order, without text, each with its `depth`. `q`: words that must all appear in a node's section, title or text |
| GET | `/api/nodes/{id}` | One node: exact text, source file, parent, applicability/exceptions (from tagging), and its cross-references in both directions |

Responses are camelCase and errors are `{ "error": "…" }`, which is what the Next.js client reads.

## Cross-references across manuals

A reference like PPM → "Section 2.6 of the CADD Users Manual" can only be linked once the CADD manual is
ingested. Upload the manuals in any order: each ingest (and each delete) re-checks every cross-manual reference,
so they settle as soon as both manuals are in. Until then they stay `awaiting_cross`.

## Layout

```
app/
  main.py            app, CORS, error shape
  config.py          settings from .env
  models.py          domain models (match the DB columns)
  schemas.py         API response shape
  deps.py            which repository / embedder to use
  routers/documents.py   upload / ingest / list / delete
  routers/nodes.py       read nodes back (Browse nodes page)
  services/uploads.py    save to bucket + record
  repository/        the only code that talks to Supabase (memory.py is the test double)
  ingestion/
    pdf_reader.py    PDF -> lines with font info
    chunker.py       lines -> nodes (heading heuristic)
    crossrefs.py     "see Section X" found by OpenAI -> cross_refs
    embeddings.py    vectors (provider is pluggable)
    resolution.py    internal pass + cross-manual pass
    pipeline.py      ingest_document: the steps above, in order
sql/schema.sql       run once in Supabase
tests/
```

## Limits to know about

- The chunker finds headings by shape and formatting (`301.1 Lane Width`, bold or larger than body text).
  It was only tried on small generated PDFs. **Run it on the real manuals and tune `HEADING_RE` / `is_heading`**
  in [app/ingestion/chunker.py](app/ingestion/chunker.py) before trusting node boundaries. Scanned PDFs need OCR first.
- Ingest runs inside the request. A large manual, especially with embeddings on, can take a while. If it gets
  slow, make it a background job.
- `EMBEDDING_PROVIDER=openai` is a placeholder choice, written but not run against the real API.
- The Supabase repository has not been run against a live project (query shapes were checked against a fake HTTP server).
- Not built yet: tagging, search, linking of tables and figures.
