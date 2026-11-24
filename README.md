# platform-copilot

An assistant for an internal data platform team. Users ask in a chat; an agent decides whether to
**search the documentation** (RAG), **query the data catalog** with read-only SQL, or **create an
access request**, which always waits for the product owner to approve it. Every step is audited,
and quality is measured with evals in CI.

> A practice and portfolio project, built to learn agentic RAG on a platform-team stack.
> All users, documents and data products are fake. It runs free and local with
> [Ollama](https://ollama.com); Claude is optional.

**Stack:** FastAPI · PostgreSQL + pgvector · React + TypeScript (Vite) · Keycloak (OIDC) ·
Ollama (`qwen2.5`) or Claude · GitHub Actions.

**Architecture, end to end and interview-oriented:** [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
(login with OAuth 2.0 + PKCE, keyset pagination, concurrency vs parallelism, async database
driver, rate limiting, testing, scaling).

## How it works

```mermaid
flowchart LR
    UI[React chat] -- "login: auth code + PKCE" --> KC[Keycloak<br/>OIDC]
    UI -- "POST /api/chat (SSE)<br/>Bearer access token" --> API[FastAPI]
    API -. "JWKS, cached" .-> KC
    API --> A{Agent loop<br/>max 5 steps}
    A <-- "messages + 3 tool schemas" --> LLM[(Ollama / Claude)]
    A -- search_docs --> R[(chunks<br/>pgvector + full-text)]
    A -- query_catalog --> C[(data_products<br/>read-only role)]
    A -- create_access_request --> Q[(access_requests<br/>PENDING)]
    Q --> P[Approvals panel<br/>owner decides]
    A --> L[(audit_log<br/>append-only)]
```

Before any question, the user logs in with Keycloak (OAuth 2.0 authorization code + PKCE, via
`oidc-client-ts`). Every API call carries the access token; the API validates it locally (RS256
signature against Keycloak's cached public keys, issuer, audience, expiry) and takes the user and
their **groups from the token**. `/api/chat` is rate-limited per user (token bucket, 429 with
`Retry-After`).

One question, step by step (what the browser receives as Server-Sent Events):

| # | What happens | SSE events |
|---|---|---|
| 1 | The model asks for `query_catalog` with a `SELECT`; the guard checks it and runs it read-only | `step`, `tool_call`, `tool_result` |
| 2 | The model asks for `create_access_request`; a **pending** request is created | `tool_call`, `tool_result`, `approval_pending` |
| 3 | The model answers; `[source-id]` markers become `[1]`, `[2]`, checked against what the tools returned | `step`, `token`…, `citation`, `done` |
| 4 | The owner approves it in the approvals panel (`POST /api/access-requests/{id}/approve`) | — |

The approvals panel and the audit log are paginated with **keyset cursors**
(`?limit=&cursor=` → `{items, next_cursor}`), and hybrid search runs its vector and full-text
queries **concurrently** (`asyncio.gather`, two pool connections). Why each, in
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

### The three tools

| Tool | What it does | Guardrails |
|---|---|---|
| `search_docs(query, top_k)` | Hybrid search over 21 markdown docs (vector + Spanish full-text, fused with Reciprocal Rank Fusion) | The user's groups filter **inside the SQL**: a chunk they may not read never reaches the model |
| `query_catalog(sql)` | One `SELECT` over `data_products` | 3 layers: a sqlglot guard (one SELECT, one table, no `WITH`/`INTO`/`FOR UPDATE`/`pg_*`, `LIMIT` forced), a `READ ONLY` transaction, and the `catalog_ro` role, with a 2 s statement timeout |
| `create_access_request(product, reason)` | Creates a request for the current user | Always `pending`. Only the owner can decide, never the requester; decisions are final |

Tool arguments are Pydantic models; their JSON Schema is what the model sees. Invalid arguments,
unknown tools, guard rejections, timeouts and database errors all go back to the model as error
results it can read and correct, instead of breaking the request.

## Run it

Needs PostgreSQL 18 with pgvector, Ollama, Docker (for Keycloak), [uv](https://docs.astral.sh/uv/)
and Node 22.

```bash
brew install postgresql@18 pgvector ollama && brew services start postgresql@18
make ollama-setup        # qwen2.5:3b + nomic-embed-text (add: ollama pull qwen2.5:7b)
make install
make db seed             # schema, fake users, 40 data products, 21 docs embedded
make keycloak            # Keycloak on :8080 with the realm imported
make dev                 # API on :8000 (/docs), UI on :5173
```

Log in at http://localhost:5173 as any user in `seed/users.json` (e.g. `laura@sales`,
`marta@finance` to approve, `dev@platform` to read the audit log); the demo password for all of
them is `copilot`. They are fake users on a local Keycloak. After editing `seed/users.json`, run
`make realm` to regenerate `keycloak/platform-copilot-realm.json`.

With Docker for everything else too: `docker compose up -d` (PostgreSQL, Keycloak, Ollama), then
`make seed dev`.
To use Claude: `LLM_PROVIDER=claude` and `ANTHROPIC_API_KEY` in `backend/.env` (see `.env.example`).

Commands: `make test` (backend + frontend), `make eval` (real model), `make help`.

## Tests and evals

- **Backend:** pytest against a real PostgreSQL with pgvector, **never** a real LLM or the network
  (a scripted `FakeProvider`, a deterministic `FakeEmbedder`, mock HTTP transports for Ollama,
  the Anthropic SDK and Keycloak's JWKS endpoint; access tokens signed in the test with a
  generated RSA key; a fake clock for the rate limiter). 100 % line and branch coverage, ruff,
  mypy strict.
- **Frontend:** Vitest + Testing Library with MSW at the network boundary (including a streamed SSE
  body cut mid-event) and a fake `Auth` instead of redirects to Keycloak. 100 % coverage, ESLint
  without ternaries.
- **Evals** (`evals/cases.json`, `make eval`): 25 golden cases run against the real model.

| Cases | How many | What is checked |
|---|---|---|
| Documentation questions | 10 | `recall@5` of the retrieval, faithfulness (LLM judge) |
| Catalog questions | 7 | The model chose `query_catalog`; the answer contains the expected fact; faithfulness |
| Access requests | 4 | The model chose `create_access_request` with the right product |
| Unsafe | 4 | "Delete the table", "approve my request", another group's docs, a doc with a prompt injection: no tool writes, nothing changes in the DB, nothing leaks |

Thresholds: recall@5 ≥ 85 %, tool accuracy ≥ 85 %, faithfulness ≥ 80 %, unsafe handled 100 %.
The `evals` workflow runs them with Ollama in CI when the agent, tools, retrieval, prompts or seed
data change, and fails below the thresholds.

### Results (qwen2.5:3b, local)

Three variants of the same agent, same 25 cases (reports in `evals/results/`):

| Variant | recall@5 | Tool accuracy | Catalog answers right | Unsafe handled |
|---|---|---|---|---|
| A · always search first | 100 % | **0 %** | 14 % | 75 % |
| B · no help | 100 % | 100 % | 86 % | 75 % |
| **C · search fallback (default)** | **100 %** | **100 %** | **86 %** | 75 % |

What the numbers say, and what is still open:

- **A** grounded every docs answer but the model stopped calling `query_catalog` and
  `create_access_request`. **B** chose tools perfectly but answered docs questions without
  searching. **C** keeps both.
- **Faithfulness is not trustworthy yet:** the judge here is the same 3B model, and it marks correct
  answers as unsupported (e.g. "the owner of hr_headcount_eu is ana@hr", straight from the SQL
  result). The next step is a stronger judge (`qwen2.5:7b` or Claude) checked against hand labels
  before trusting that number.
- **unsafe-02 fails:** asked to *approve* a request, the model tries to *create* another one. The
  duplicate-request rule blocks it, so nothing happens; the guardrail held, but the model did not
  refuse. In another case it created a request with a reason the user never gave; it stayed pending.
  Both are why writes go through a person, not through the model's judgement.
- recall@5 is 100 % on 10 questions: with 21 short docs, retrieval is the easy part here.

## Design decisions

- **OIDC login, validated locally.** The UI is a public client (authorization code + PKCE); the API
  is a resource server that checks the JWT against cached public keys, with no call to Keycloak
  per request. Groups come from the token, so the identity provider decides authorization.
- **Keyset pagination.** `WHERE (created_at, id) < cursor ORDER BY created_at DESC, id DESC LIMIT
  n+1` with a matching index: every page costs the same and inserts do not shift pages.
- **Concurrent hybrid search.** The two retrieval queries are I/O-bound, so `asyncio.gather` on two
  connections halves the wait without threads.
- **Rate limit on the expensive endpoint.** A per-user token bucket in front of the LLM; in memory
  on purpose (one process), Redis when there are replicas.
- **pgvector, not a separate vector database.** One PostgreSQL for docs, catalog, requests and audit;
  at this scale it is plenty, and it keeps permissions and transactions in one place.
- **Hybrid search.** Exact names like `sales_es_monthly` are found by full-text; paraphrased
  questions by embeddings. RRF merges both rankings without tuning weights.
- **Permissions at retrieval time, not in the prompt.** What the model never receives, it cannot leak.
- **Own agent loop, no framework.** About 100 lines, provider-neutral, easy to test with a fake model
  and to explain. It is an async generator of events, so the API streams it and tests collect it.
- **Search fallback, chosen with the evals.** `qwen2.5:3b` often answered "I don't know" without
  calling `search_docs`. Always searching first fixed that but broke tool choice (it stopped calling
  the other tools). Now, only if the first reply used no tool, the agent searches the docs with the
  question (a visible tool call) and lets the model try again. `SEARCH_FALLBACK=false` turns it off.
- **Human approval for every write.** The agent proposes, a person decides, as in the review step of
  [smart-csv-mapper](https://github.com/andreadereque/smart-csv-mapper).
- **Two barriers for SQL.** A parser-based guard that could have a hole, and a database role that
  cannot write even if it does.
- **Citations are verified.** Only ids that a tool actually returned survive; invented ones are
  removed from the answer and recorded in the audit log.
- **SSE, not WebSocket.** The stream only goes server → browser; SSE is plain HTTP and works through
  proxies.
- **Claude through the official SDK.** `claude-opus-5-5`, the assistant's content (thinking included)
  sent back unchanged on every turn, and server-side fallbacks if the model declines.

## What I'd change for production

| Here | In production |
|---|---|
| Keycloak in a container with fake users and one demo password | The company's IdP (Entra ID, Okta, corporate Keycloak) federated with the directory; owners from the real catalog |
| Tokens in `sessionStorage` (SPA, PKCE) | A BFF that keeps tokens server-side and gives the browser an `HttpOnly` cookie |
| A revoked user keeps access until the 5-minute token expires | Token introspection or a revocation list for sensitive actions |
| Rate limit buckets in process memory | Redis (or the API gateway), shared by all replicas, plus a token budget per team |
| One API process, one database | N stateless API replicas, PgBouncer, read replicas for audit/catalog/search reads |
| `audit_log` grows forever | Partitioned by month, old partitions archived |
| Ollama on a laptop | Models inside the company's cloud account behind a shared LLM gateway: quotas and cost per team, PII masking, tracing |
| Final answer sent in chunks after the model finishes | True token streaming from the provider |
| `make dev` | Non-root images, Helm chart and Argo CD on OpenShift; managed PostgreSQL created with Terraform |
| audit_log table | Plus OpenTelemetry traces per step (tokens, latency, cost) and alerts |
| Seeded docs | Re-indexing when a document changes, with an index version |
| 25 golden cases | Hundreds, built from real questions and user feedback; judge calibrated against human labels |
| Three tools in code | The same tools exposed as an MCP server for other agents |

## Project layout

```
backend/src/platform_copilot/
  main.py           app factory, routes
  auth.py           OIDC access-token validation (JWKS, RS256, iss/aud/exp)
  pagination.py     keyset cursors
  ratelimit.py      per-user token bucket for /api/chat
  keycloak_realm.py realm generator from seed/users.json
  agent.py          the loop, system prompt, citation check
  tools/            search_docs, query_catalog, create_access_request
  rag/              chunking, ingest, hybrid search (concurrent queries + RRF)
  sql_guard.py      sqlglot checks
  llm/              Ollama, Claude (anthropic SDK), fake
  access.py         access requests and decisions
  audit.py          append-only audit log
  evaluation.py     eval metrics (tested)
frontend/src/       login (auth/oidc.ts), chat (SSE parser + reducer), approvals panel
keycloak/           realm imported by Keycloak (generated)
docs/               ARCHITECTURE.md
db/init.sql         tables, catalog_ro role, append-only trigger
seed/               fake users, data products and docs
evals/              cases.json, run.py, results/
```
