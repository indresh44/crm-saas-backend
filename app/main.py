from fastapi import FastAPI

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

app.include_router(auth_router, prefix="/api/v1", tags=["auth"])
app.include_router(businesses_router, prefix="/api/v1", tags=["businesses"])
app.include_router(users_router, prefix="/api/v1", tags=["users"])
app.include_router(catalog_items_router, prefix="/api/v1", tags=["catalog_items"])
app.include_router(chat_router, prefix="/api/v1", tags=["chat"])
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


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
