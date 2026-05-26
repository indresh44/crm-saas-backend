from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.v1.agent_chat import router as agent_chat_router
from app.api.v1.auth import router as auth_router
from app.api.v1.attachments import router as attachments_router
from app.api.v1.bookings import router as bookings_router
from app.api.v1.businesses import router as businesses_router
from app.api.v1.catalog_items import router as catalog_items_router
from app.api.v1.chat import router as chat_router
from app.api.v1.customers import router as customers_router
from app.api.v1.dashboard import router as dashboard_router
from app.api.v1.users import router as users_router
from app.api.v1.invoices import router as invoices_router
from app.api.v1.invoice_templates import router as invoice_templates_router
from app.api.v1.lead_activities import router as lead_activities_router
from app.api.v1.lead_followups import router as lead_followups_router
from app.api.v1.leads import router as leads_router
from app.api.v1.messages import router as messages_router
from app.api.v1.meetings import router as meetings_router
from app.api.v1.notifications import router as notifications_router
from app.api.v1.payments import router as payments_router
from app.api.v1.pipeline_stages import router as pipeline_stages_router
from app.api.v1.pipelines import router as pipelines_router
from app.api.v1.quotes import router as quotes_router
from app.api.v1.tasks import router as tasks_router
from app.api.v1.whatsapp_accounts import router as whatsapp_accounts_router
from app.api.v1.whatsapp_conversations import router as whatsapp_conversations_router
from app.api.v1.whatsapp_messages import router as whatsapp_messages_router
from app.api.v1.public_invoices import router as public_invoices_router
from app.api.v1.onboarding import router as onboarding_router
from app.api.v1.whatsapp_webhooks import router as whatsapp_webhooks_router
from app.core.actor_context import set_actor_context
from app.core.config import settings
from app.models.enums import ActorType
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(
    title="CRM API",
    version="0.1.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
)

origins = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://192.168.1.3:3000", 
    "https://19e4-2401-4900-8820-61b6-4d68-ca00-3dd0-ff52.ngrok-free.app",
    "*"
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def actor_context_middleware(request: Request, call_next):
    """Default every HTTP request to ActorType.HUMAN for the duration of the
    handler. Inner scopes (the multi-task runner sets TASK, the agent loop
    sets AI, the webhook handler sets SYSTEM) re-bind the context for their
    own work; on exit the outer HUMAN restores. Activity rows created
    anywhere downstream see the correct actor with no per-route plumbing.

    Webhook routes that should NOT be HUMAN (e.g. WhatsApp webhooks) wrap
    their own bodies in `set_actor_context(SYSTEM)` — that inner bind wins
    for the duration of the handler. The outer HUMAN here is harmless if a
    webhook never emits an activity at HTTP-handler scope (it doesn't)."""
    with set_actor_context(ActorType.HUMAN):
        return await call_next(request)


@app.exception_handler(ValueError)
async def value_error_handler(_request: Request, exc: ValueError) -> JSONResponse:
    """
    Convert uncaught ValueErrors into well-formed 400 JSON responses
    so frontends see a readable message instead of an opaque 500/CORS failure.
    """
    return JSONResponse(status_code=400, content={"detail": str(exc)})


app.include_router(auth_router, prefix="/api/v1", tags=["auth"])
app.include_router(businesses_router, prefix="/api/v1", tags=["businesses"])
app.include_router(users_router, prefix="/api/v1", tags=["users"])
app.include_router(catalog_items_router, prefix="/api/v1", tags=["catalog_items"])
app.include_router(chat_router, prefix="/api/v1", tags=["chat"])
app.include_router(agent_chat_router, prefix="/api/v1", tags=["agent-chat"])
app.include_router(customers_router, prefix="/api/v1", tags=["customers"])
app.include_router(dashboard_router, prefix="/api/v1", tags=["dashboard"])
app.include_router(leads_router, prefix="/api/v1", tags=["leads"])
app.include_router(lead_activities_router, prefix="/api/v1", tags=["lead-activities"])
app.include_router(lead_followups_router, prefix="/api/v1", tags=["lead-followups"])
app.include_router(pipelines_router, prefix="/api/v1", tags=["pipelines"])
app.include_router(pipeline_stages_router, prefix="/api/v1", tags=["pipeline-stages"])
app.include_router(quotes_router, prefix="/api/v1", tags=["quotes"])
app.include_router(bookings_router, prefix="/api/v1", tags=["bookings"])
app.include_router(invoices_router, prefix="/api/v1", tags=["invoices"])
app.include_router(invoice_templates_router, prefix="/api/v1", tags=["invoice-templates"])
app.include_router(payments_router, prefix="/api/v1", tags=["payments"])
app.include_router(tasks_router, prefix="/api/v1", tags=["tasks"])
app.include_router(attachments_router, prefix="/api/v1", tags=["attachments"])
app.include_router(notifications_router, prefix="/api/v1", tags=["notifications"])
app.include_router(onboarding_router, prefix="/api/v1", tags=["onboarding"])
app.include_router(messages_router, prefix="/api/v1", tags=["messages"])
app.include_router(meetings_router, prefix="/api/v1", tags=["meetings"])
app.include_router(whatsapp_accounts_router, prefix="/api/v1", tags=["whatsapp-accounts"])
app.include_router(whatsapp_conversations_router, prefix="/api/v1", tags=["whatsapp-conversations"])
app.include_router(whatsapp_messages_router, prefix="/api/v1", tags=["whatsapp-messages"])
app.include_router(whatsapp_webhooks_router, prefix="/api/v1", tags=["whatsapp-webhooks"])
app.include_router(public_invoices_router, prefix="/api/public", tags=["public-invoices"])

# --- TEST / DEBUG ONLY ----------------------------------------------------
# Disposable read-model chat endpoint (POST /api/test-chat). Crude token auth,
# no login. NOT a real API route — see app/read_model/tools/test_chat_api.py.
from app.read_model.tools.test_chat_api import router as test_chat_router  # noqa: E402

app.include_router(test_chat_router, prefix="/api", tags=["TEST-DEBUG"])
# --------------------------------------------------------------------------


# Admin router is only registered when explicitly enabled via env var.
# In production neither ENABLE_ADMIN_ROUTES nor SUPER_ADMIN_EMAILS is set,
# so /api/admin/* returns 404 — the routes literally do not exist.
# See Docs/plans/admin-panel.md.
if settings.ENABLE_ADMIN_ROUTES:
    from app.api.admin import router as admin_router

    app.include_router(admin_router, prefix="/api/admin")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
