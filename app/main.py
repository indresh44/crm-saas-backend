from fastapi import FastAPI

from app.api.v1.attachments import router as attachments_router
from app.api.v1.bookings import router as bookings_router
from app.api.v1.customers import router as customers_router
from app.api.v1.invoices import router as invoices_router
from app.api.v1.lead_activities import router as lead_activities_router
from app.api.v1.leads import router as leads_router
from app.api.v1.messages import router as messages_router
from app.api.v1.notifications import router as notifications_router
from app.api.v1.payments import router as payments_router
from app.api.v1.pipeline_stages import router as pipeline_stages_router
from app.api.v1.pipelines import router as pipelines_router
from app.api.v1.quotes import router as quotes_router
from app.api.v1.tasks import router as tasks_router

app = FastAPI(
    title="CRM API",
    version="0.1.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
)

app.include_router(customers_router, prefix="/api/v1", tags=["customers"])
app.include_router(leads_router, prefix="/api/v1", tags=["leads"])
app.include_router(lead_activities_router, prefix="/api/v1", tags=["lead-activities"])
app.include_router(pipelines_router, prefix="/api/v1", tags=["pipelines"])
app.include_router(pipeline_stages_router, prefix="/api/v1", tags=["pipeline-stages"])
app.include_router(quotes_router, prefix="/api/v1", tags=["quotes"])
app.include_router(bookings_router, prefix="/api/v1", tags=["bookings"])
app.include_router(invoices_router, prefix="/api/v1", tags=["invoices"])
app.include_router(payments_router, prefix="/api/v1", tags=["payments"])
app.include_router(tasks_router, prefix="/api/v1", tags=["tasks"])
app.include_router(attachments_router, prefix="/api/v1", tags=["attachments"])
app.include_router(notifications_router, prefix="/api/v1", tags=["notifications"])
app.include_router(messages_router, prefix="/api/v1", tags=["messages"])


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}

