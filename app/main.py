from fastapi import FastAPI

from app.api.v1.customers import router as customers_router

app = FastAPI(title="CRM API",version="0.1.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",)
app.include_router(customers_router, prefix="/api/v1", tags=["customers"])


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}

