# SellNSettle — Backend

FastAPI backend for **SellNSettle**, a chat-first AI CRM for Indian MSMEs (interior designers, photographers, coaches, contractors) in Tier 2/3 cities. Users run their whole business cycle — enquiry → follow-up → quote → invoice → payment — by chatting in Hindi, English, or Hinglish.

Frontend repo: `desi-saas-frontend` (Next.js).

## Tech Stack

- **Framework:** FastAPI + SQLModel + PostgreSQL
- **Architecture:** Strict 4-layer separation — API Route → Service → Repository → Model
- **AI Chat:** LiteLLM + Gemini 2.5 Flash, 32 tools (25 read-only, 7 write with a two-stage confirm flow)
- **PDF generation:** WeasyPrint + Jinja2 (invoices/estimates)
- **File storage:** Cloudflare R2 (S3-compatible, via boto3)
- **Auth:** bcrypt + PyJWT (access + refresh tokens)
- **Migrations:** Alembic
- **Email:** ZeptoMail REST API (falls back to stdout in dev when unconfigured)

## Architecture

Every feature is built through the same four layers:

```
API Route  →  Service  →  Repository  →  Model
```

- `app/api/` — FastAPI routers, one file per resource (leads, invoices, payments, customers, catalog, etc.)
- `app/services/` — business logic
- `app/repositories/` — DB access
- `app/models/` — SQLModel table definitions
- `app/read_model/` — schema-driven, tenant-scoped query engine the AI chat uses for read-only questions
- `app/write_surface/` — capability registry + prepare/commit engine backing the chat's two-stage write flow
- `app/agent/` — the ReAct loop that drives the AI chat
- `migrations/` — Alembic migration history

## Core Domain

- **Leads/Enquiries** — stage pipeline (New Enquiry → Site Visited → Quote Sent → Approved → Work in Progress → Invoiced → Paid), notes, activity log
- **Invoices** — invoice-as-quote model: a `draft`/`sent` invoice renders as an **Estimate**, an `approved`/`partial`/`paid` invoice renders as a **Tax Invoice**. No separate Quote entity.
- **Payments** — recorded against invoices, edit-in-place metadata, void + replace for amount corrections, per-customer outstanding tracking
- **Follow-ups, Catalog, Invoice Templates, Analytics** — see the domain docs below

## Running Locally

Requires Python 3.11+, PostgreSQL, and system libraries for WeasyPrint (Cairo/Pango — see `Dockerfile` for the exact `apt` packages on Debian, or use Docker instead of a bare-metal install).

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
pip install -r requirements.txt

# copy the env vars below into a .env file, then:
alembic upgrade head
uvicorn app.main:app --reload
```

Or via Docker Compose (spins up Postgres + Redis alongside the API):

```bash
docker compose up --build
```

### Environment Variables

All settings live in `app/core/config.py` (Pydantic `BaseSettings`, loaded from `.env`). None are committed to the repo. Minimum to get a working local instance:

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | PostgreSQL connection string |
| `JWT_SECRET_KEY` | Signing key for access/refresh tokens — **must** be overridden in any real deployment |
| `R2_ENDPOINT_URL`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET_NAME`, `R2_PUBLIC_URL` | Cloudflare R2 for attachments/catalog images |
| `ZEPTOMAIL_TOKEN` | Transactional email; leave unset to log emails to stdout instead |
| `FRONTEND_BASE_URL` | Used to build links in emails / public share URLs |

See `app/core/config.py` for the full list (WhatsApp Cloud API, admin panel flags, password-reset timing, etc.) — everything has a safe default for local dev except the JWT key.

## Tests

```bash
pytest
```

Test files are co-located per module (`app/agent/tests/`, `app/read_model/tests/`, `app/services/tests/`, `app/write_surface/tests/`), including Postgres-backed integration suites for the agent loop, invoices, payments, and follow-ups.
