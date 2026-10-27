# Ingestion flow

One PDF at a time: save it to the Supabase bucket, then cut it into chunks and save those in the database.

```mermaid
flowchart TD
  A["Client: pick one PDF and its manual"] --> B["POST /api/documents<br/>stream to temp file, SHA-256, check %PDF header"]
  B --> D["Supabase Storage<br/>manuals/MANUAL/id.pdf"]
  D --> E["source_documents row<br/>status = uploaded"]
  E --> F["Client calls<br/>POST /api/documents/id/ingest"]

  subgraph ING["Ingest (one request)"]
    G["Download the PDF from Storage"] --> H["Read lines + fonts (PyMuPDF)<br/>drop page numbers and running headers"]
    H --> I["Find numbered headings<br/>cut one node per section, text kept verbatim"]
    I --> J["OpenAI finds 'see Section X' mentions<br/>(code checks each is in the text), save to cross_refs"]
    J --> K["Embed each node (vector)<br/>skipped unless a provider is set"]
    K --> L[("Save nodes + cross_refs<br/>to Supabase")]
    L --> M["Internal pass<br/>link same-manual refs to real nodes"]
    M --> N["Cross-manual pass<br/>re-check refs into other manuals"]
  end

  F --> G
  N --> O["status = ingested<br/>summary: counts"]
  G -. "any error" .-> P["status = failed<br/>error saved on the row"]
```

## How headings are found

The PDF is read page by page. For each page the planner (OpenAI, or the regex fallback) is shown:

| Block | Lines | Numbered? | Purpose |
| --- | --- | --- | --- |
| End of the previous page | last `CHUNK_CONTEXT_LINES` (default 6) | no | context only: is the page continuing a list or a table? |
| The page itself | all | yes | the only place a heading may start |
| Start of the next page | first `CHUNK_CONTEXT_LINES` | yes, continuing the page's numbers | a heading that starts at the bottom of the page can run into these |

The planner answers with line numbers only (where each heading starts and how many lines it takes), and the code cuts the
PDF's own lines there. A node runs until the next heading, even across pages. A heading cut by a page break is joined
from both pages; the lines it took are removed from the next page before that page is planned. The code drops or trims
any answer it cannot trust: a start outside the page, an overlap, a section number that is not in the heading text, or
heading lines that are not set in the same style as the first one.

## Cross-reference statuses

| Status | Meaning |
| --- | --- |
| `unresolved` | Points into the same manual and the internal pass did not find the section. So do tables and figures that were not captured. Also: points at another manual that **is** ingested but has no such section. |
| `awaiting_cross` | Points at another manual that is **not ingested yet** (e.g. PPM citing "Section 2.6 of the CADD Users Manual"). |
| `resolved` | Linked to a real node, or to a captured figure (`target_figure_id`) or table (`target_table_id`). `resolved_by` records which pass did it. |

The cross-manual pass runs on every ingest and every delete, over the current ingested document of each manual, and
recomputes each cross-manual reference from scratch. Upload order does not matter, and replacing or deleting a manual
puts references to it back to `awaiting_cross`.

## Figures

`ingestion/figures.py` runs on the same PDF during ingest. A block starting with `Figure N-N` is a caption when graphics
(embedded images or vector drawings) sit within 60 points above or below it and the words after the number do not start
in lower case ("Figure 2-7 shows..." is a sentence). The drawing plus the caption is rendered to a PNG
(`FIGURE_DPI`, default 150) and saved in the bucket at `figures/<document id>/<label>.png`; a row in `figures` holds the
label, caption, page, owning section and image path. The first usable caption of a number wins.

The internal and cross-manual passes link a `... Figure N-N` reference to the figure with that number. Endpoints:
`GET /api/figures?document_id=` and `GET /api/figures/{id}/image`. A failure while capturing figures does not fail the
ingest; it is logged and shown as `figures_error` in the document summary. Run `sql/002_figures.sql` on an existing database.

## Tables

`ingestion/tables.py` runs on the same PDF during ingest. A line starting with `Table N` is a caption when a **ruled** grid
(PyMuPDF's table finder, so it needs ruling lines) sits within 60 points of it, overlapping it horizontally, with at
least two non-empty rows, and the words after the number do not start in lower case ("Table 2.4 lists..." is a
sentence). The cells are saved as `rows` in `manual_tables`, and the grid plus the caption is rendered to a PNG
(`TABLE_DPI`, default 150) in the bucket at `tables/<document id>/<label>.png`. The table's text also stays in its
node: this record is only what a reference can point at.

The internal and cross-manual passes link a `... Table N` reference to the table with that number. Endpoints:
`GET /api/tables?document_id=` (no cells), `GET /api/tables/{id}` (with cells) and `GET /api/tables/{id}/image`. A
failure while capturing tables does not fail the ingest; it is logged and shown as `tables_error` in the summary.
Run `sql/005_tables.sql` on an existing database **before** the next ingest, then ingest each document again.

Limits: a table with no ruling lines (many Caltrans tables have horizontal rules only) is not captured, and a reference
to it stays `unresolved`. Only the part of a table on its caption's page is kept. A reference to a family of tables
("Table 202.2" for 202.2A to 202.2E) does not match any of them.

## Retrieval

The chat Lambda (`infra/lambdas/chat/retrieval.py`) runs three searches **at the same time** (each is a network round trip; none
narrows the others) and merges them, so a question can be worded any way:

| Search | Finds | SQL |
| --- | --- | --- |
| meaning | paraphrases: nearest section vectors by cosine | `match_nodes` (`sql/003_match_nodes.sql`) |
| keywords | the question's words in the text, rare words counting more. Full-text rank with an exact-phrase bonus, or BM25 (below) | `match_nodes_text` (`sql/004_hybrid_search.sql`) or `match_nodes_bm25` (`sql/paradedb/001_nodes_bm25.sql`, on ParadeDB) |
| heading | a section number (`1-1.8`) or a heading typed in full or in part (`§1-1.8 Content and Development of Electronic Files`) | `match_nodes_title` (`sql/004_hybrid_search.sql`) |

The lists are merged with reciprocal rank fusion (equal scores share a rank; an exact heading or number is pinned first;
only keyword matches near the best one count). Each hit says which searches found it (`matchedBy`). If `004` has not been
run, keywords and headings are skipped and search is meaning-only. Chat answers only from relevant hits: close in meaning,
heading named, or the words present; otherwise it says the manuals do not cover the question. Conversational words ("I want to know the article ...") are ignored by the keyword search. A "where / which section" question searches wider (`CHAT_LOCATE_TOP_K`) and asks the model to list every place; a long section is shortened to its start plus the passages that hold the question's words; only the sources the answer cites are returned.

### Keyword backend: full-text rank or BM25 (ParadeDB)

`KEYWORD_BACKEND` is `fts` (default) or `paradedb`; the other two searches and the merge do not change. Supabase has no
`pg_search`, so `paradedb` keeps a copy of the sections' text on a **separate ParadeDB database** and the chat Lambda asks it
over a direct Postgres connection (PostgREST cannot reach it):

```
ingest ──> Supabase (nodes, vectors, graph) ──logical replication / sync_paradedb.py──> ParadeDB (id, document, manual, title, text)
chat Lambda ──> Supabase RPC: meaning + heading search          ──┐
            └─> psycopg ──> ParadeDB: match_nodes_bm25 (BM25)   ──┴─> merge (RRF) -> answer
```

Set up (a test environment; nothing here changes Supabase until you run `002`):

1. **A ParadeDB database** the Lambda can reach: see "Hosting ParadeDB" below. For trying it on your machine:
   `docker compose -f paradedb/docker-compose.yml up -d` (listens on 127.0.0.1 only; not reachable from AWS).
2. **The table, index and function:** `psql "$PARADEDB_URL" -f sql/paradedb/001_nodes_bm25.sql`.
3. **Fill it**, one of two ways: `python scripts/sync_paradedb.py` (copies from Supabase; `--check` compares counts; run it
   after each ingest), or logical replication with `sql/paradedb/002_replication.sql` (changes arrive by themselves).
4. **Connect the Lambda:** put `PARADEDB_URL=postgresql://user:password@host:5432/db?sslmode=require` in `server/.env`, run
   `python scripts/set_secrets.py` (copies it into the secret), then `cdk deploy -c keyword_backend=paradedb`.
   Locally, set `KEYWORD_BACKEND=paradedb` and `PARADEDB_URL`.

#### Hosting ParadeDB (for the deployed chat)

The Lambda is not in a VPC, so the database must accept connections from the internet, over TLS. Where to run it
(per ParadeDB's deploy docs, https://docs.paradedb.com/deploy/overview; check them, they change):

- **The Community Docker image on a small server or a container platform** (a VPS, Railway, Render, DigitalOcean). ParadeDB
  calls this suitable for development and staging. This is the option below.
- ParadeDB Cloud (fully managed) was not available yet (waitlist). For production the docs point to ParadeDB Enterprise
  (Kubernetes, or BYOC in your own AWS or GCP account), which means contacting their sales.
- Not Neon: its `pg_search` is deprecated (its replacement is a different extension, not ParadeDB).

On a server with Docker (tested locally with Docker 28; not yet on a public server):

1. Copy `server/paradedb/` and `server/sql/paradedb/` to the server, keeping their relative paths.
2. A certificate: `mkdir -p certs && openssl req -new -x509 -days 825 -nodes -subj "/CN=YOUR-HOST" -keyout certs/server.key -out certs/server.crt`
3. A password: `export PARADEDB_PASSWORD="$(openssl rand -base64 33 | tr -d '/+=')"` (keep it: it goes into `PARADEDB_URL`).
4. `docker compose -f docker-compose.server.yml up -d`. First start creates the database and runs `001_nodes_bm25.sql`.
   Network connections need TLS and the password (`pg_hba.conf`); a plain connection is refused.
5. In the server's firewall or security group, allow inbound TCP 5432 from the internet. The Lambda has no fixed address, so
   you cannot allow only its address; TLS, a long password and the read-only role below are what protect it.
6. Give the Lambda a read-only role, so a leaked connection string cannot change or delete anything (tested):

   ```sql
   create role chat_reader login password 'A-DIFFERENT-LONG-PASSWORD';
   grant connect on database nodes_search to chat_reader;
   grant usage on schema public to chat_reader;
   grant select on nodes to chat_reader;
   grant execute on function match_nodes_bm25(text, int, text) to chat_reader;
   ```

   Then `PARADEDB_URL=postgresql://chat_reader:...@YOUR-HOST:5432/nodes_search?sslmode=require` for the Lambda, and the owner
   (`search`) only for `001`, `sync_paradedb.py` and replication.

`sslmode=require` encrypts the connection but does not prove which server answered: someone in the middle could present
their own certificate. To verify the server, give it a real certificate (for example Let's Encrypt for a domain name that
points at it) and use `sslmode=verify-full`; not tested here. A stronger production layout puts ParadeDB in the same AWS VPC
as the Lambda, with no public address: that needs the Lambda moved into the VPC (a CDK change) and is not done.
The data is only a copy of what is in Supabase: if the server is lost, run `001` on a new one and `sync_paradedb.py`.
ParadeDB's docs say the Community edition has no write-ahead-log support for its index; I did not test what a crash does to
it, but `REINDEX` or a fresh `sync_paradedb.py` rebuilds it.

How it behaves:

- The question's filler is removed first (`app/repository/keywords.py`): standard English stop words and words that only ask
  ("want", "know", "article", "section"). They are rare in the manuals, so BM25 would weigh them heavily.
- A section matches if it has any of the remaining words, stemmed in English. BM25 weighs a word by how rare it is,
  saturates repeats and favours shorter sections; a word in the heading counts twice as much as one in the text. The score
  is averaged over the question's words so a longer question does not score higher for having more of them.
- Relevance: a keyword hit counts as evidence from `KEYWORD_MIN_SCORE_BM25` (4.0) up, against 0.3 for the full-text rank,
  because the scales differ. Measured on the HDM and PPM together (1,094 sections), with ParadeDB's real scores: the best
  hit of 14 real questions scored 5.5 to 15.6; of 14 off-topic ones 11 scored under 4, and 3 passed because they contain
  words that really appear in the manuals ("capital", "latest", "file"). The cosine check and the model still judge those.
  BM25 weights grow with the size of the corpus, so check the value again if the loaded manuals change a lot.
- If ParadeDB cannot be reached (5 s limit, one retry on a fresh connection), the keyword search returns nothing and the
  error is logged; meaning and heading search carry on. If `PARADEDB_URL` is missing the chat answers 500 with a message that says so.
- Freshness: a copy made by `sync_paradedb.py` is as old as its last run; replication is behind by about a second.
- Tests: `MemoryRepository("paradedb")` is a stand-in that ranks like the real index. `tests/test_paradedb_integration.py`
  runs the real SQL file and class against a ParadeDB (`PARADEDB_TEST_URL=postgresql://... pytest ...`; it uses a
  throwaway schema and is skipped otherwise).

### Chat from the FastAPI server (no AWS)

`POST /api/chat` on the ingest server (`server/app/routers/chat.py`) gives the same answer as the chat Lambda. It does not
copy the chat logic: it loads `chat_service`, `retrieval` and `llm_client` from `infra/lambdas/chat`, so the two cannot drift
apart (`tests/test_chat_endpoint.py` runs the real Lambda handler and this endpoint on the same data and compares the
JSON). The Lambda is untouched and can still be deployed. Together with a local ParadeDB there is nothing to host:

1. `docker compose -f paradedb/docker-compose.yml up -d` (the first start creates the table, index and function).
2. `PARADEDB_URL=postgresql://postgres:test@127.0.0.1:55432/nodes_search python scripts/sync_paradedb.py`
   (again after each ingest; `--check` compares counts).
3. In `server/.env`: `KEYWORD_BACKEND=paradedb` and the same `PARADEDB_URL`; restart the server. It also needs
   `EMBEDDING_PROVIDER=openai` and `OPENAI_API_KEY` (the question is embedded and the answer is written by the model).
4. In `client/.env.local`: `CHAT_API_URL=http://localhost:8000/api` and `CHAT_API_KEY` set to anything (to the value of
   `CHAT_API_KEY` in `server/.env` if you set one there): the Next.js route adds `/chat`.

Request and response are the Lambda's (`{message, history?, manual?}` -> `{answer, covered, model, sources[]}`). Differences:
a malformed request is a 422 here (400 in the Lambda), and the API key is optional: with `CHAT_API_KEY` empty the endpoint is
open, which is only safe while the server listens on your own machine. A missing embedder, key or `infra/` folder is a 503
that says what to set.

With `expand` each hit also carries what its resolved cross-references lead to: `mentions` (sections its text points at),
`mentioned_by` (sections pointing at it), and figures (`in_section`, `mentioned`). A linked section that is itself a hit
has `alsoHit: true`. Unresolved references are not followed.

## Failure handling

An exception during ingest marks the document `failed` with the reason in `error` (a PDF with no numbered headings
gives a 422 with a plain explanation). The file stays in the bucket. Run ingest again, from the UI "Retry ingest",
after fixing the cause. Re-running replaces the document's chunks, never duplicates them.

## Not built yet

- Tagging (classification, applicability conditions, exceptions). The `node_conditions` table and the tagging columns on `nodes` exist; nothing fills them.
- Search and the explicit "not covered" answer.
- Tables without ruling lines, tables that run over several pages, and reading a figure's or table's content with a vision model.
- Tables and figures in search and chat answers (they are only linked from references so far).
- Requirement class from formatting (boldface / underlined).
