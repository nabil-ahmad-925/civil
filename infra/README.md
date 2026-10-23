# infra: AWS backend for the chat

AWS CDK (Python) app. Two stacks, one file each in `stacks/`, combined in `app.py`:

| Stack (id) | File | Holds |
| --- | --- | --- |
| `CivilEngLambda` | `lambda_stack.py` | The chat and health Lambdas and the Secrets Manager secret |
| `CivilEngApi` | `api_gateway_stack.py` | HTTP API: routes, CORS, rate limit |

`bundling.py` (next to `app.py`) builds each Lambda's package. Nothing else is deployed: no database, bucket or user
pool (Supabase holds the data; sign-in is the API key).

```
Next.js server --x-api-key--> API Gateway (HTTP API) --> Lambda chat   POST /chat
                                                     --> Lambda health GET  /health
Lambdas --> Secrets Manager (civil-eng/chat)   OpenAI key, Supabase URL + service key, CHAT_API_KEY
        --> Supabase (nodes, cross_refs, figures, bucket)   and   OpenAI (embeddings + answer)
```

- **chat**: embeds the question, finds the nearest sections, follows their resolved cross-references (sections and
  figures), asks the model to answer only from those numbered sources, and returns the answer with the sources and
  temporary links to the figure pictures. If nothing is close enough it says the manuals do not cover it, without
  calling the model.
- Each Lambda has its own folder and its own package:

  ```
  lambdas/
    chat/    handler.py  chat_service.py  retrieval.py  llm_client.py  requirements.txt     POST /chat
    health/  handler.py                                                                    GET  /health (standard library only, deployed as is)
    shared/  lambda_common.py     secrets, key check, parsing; copied next to the chat handler
  ```

  The entry point of every function is `handler.handler`; a handler is the thin HTTP layer. The chat logic sits in the
  chat folder: `chat_service.py` (ask for sources, build the prompt, ask the model, pick the cited sources),
  `retrieval.py` (meaning + keyword + heading search merged, plus cross-reference expansion) and `llm_client.py` (the
  OpenAI call). The code the Lambda shares with the ingest server (repository, models, embeddings) stays in `server/app/`,
  and `bundling.py` lists which of those files the package gets, so it stays small. Any `.py` file in a function's folder
  is packed with it. After changing a file in `server/app/` that the Lambda uses, run `npm run deploy` again: the Lambda
  has its own copy.
  To add a Lambda: make `lambdas/<name>/handler.py` (+ `requirements.txt` if it needs packages), add a `LambdaSpec` in
  `bundling.py`, a function in `stacks/lambda_stack.py` and a route in `stacks/api_gateway_stack.py` (pass the function ARN from `app.py`).

## Deploy

Needs Node (for the `cdk` CLI), Python 3.13 and AWS credentials.

```bash
cd infra
python3.13 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
npm install                        # installs the cdk command line tool into infra/node_modules
npx cdk bootstrap --profile civil-eng   # once per account/region
npm run deploy                     # = cdk deploy --all --profile civil-eng: both stacks, in dependency order; prints ApiUrl

python scripts/set_secrets.py --show-api-key   # copies the OpenAI/Supabase values from server/.env into the secret
```

`npm run deploy` always uses the AWS profile `civil-eng` (edit `package.json` to change it) and runs the app with
`.venv/bin/python`, so the virtualenv does not need to be active for it. `set_secrets.py` does need it, and
`export AWS_PROFILE=civil-eng` in that terminal.

Then run `server/sql/003_match_nodes.sql` and `server/sql/004_hybrid_search.sql` in the Supabase SQL editor (the search functions the chat Lambda calls: meaning, keywords, headings), and put
the two values in `client/.env.local`:

```
CHAT_API_URL=<ApiUrl output>
CHAT_API_KEY=<value printed by set_secrets.py>
```

Restart `npm run dev`; the Chat page now answers from the manuals.

## Settings

In `cdk.json` `context`, or per deploy with `-c name=value`:

| Context | Default | Meaning |
| --- | --- | --- |
| `cors_origins` | `["http://localhost:3000"]` | Allowed browser origins (`-c cors_origins=https://a.com,https://b.com`) |
| `chat_model` | `gpt-4.1-mini` | Model that writes the answer |
| `api_throttle_rate` / `api_throttle_burst` | 10 / 20 | Requests per second the API accepts |

Retrieval knobs (`CHAT_TOP_K`, `CHAT_MIN_SCORE`, ...) are fields of `server/app/config.py`; set them as Lambda
environment variables in `stacks/chat_stack.py` if you want to change them.

## Test and inspect

```bash
pytest                                  # template assertions for both stacks, no AWS needed (skips building dependencies)
CDK_OUTDIR=cdk.out python app.py        # builds the real package into cdk.out/ so you can look inside
cdk diff --all
cdk deploy CivilEngApi                  # one stack (its dependencies must already be deployed)
```

The handlers are tested in `server/tests/test_lambda_handlers.py` (in-memory repository, fake model).

## Notes

- Every route except `/health` needs the `x-api-key` header. The key is generated by CDK into the secret; no credential
  is in the template or in git. The browser never sees it: `client/src/app/api/chat/route.ts` adds it on the server.
- Lambdas are not in a VPC (Supabase and OpenAI are reached over the internet).
- `cdk destroy --all` removes the rest except the secret's 30-day recovery copy (re-creating the secret with the same
  name has to wait for it, or use `aws secretsmanager delete-secret --force-delete-without-recovery`).
