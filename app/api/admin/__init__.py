"""
Admin endpoints — local-only, never registered in production.

The router exported here is only included by `app.main` when
`settings.ENABLE_ADMIN_ROUTES` is true. Production env never sets that
flag, so /api/admin/* returns 404. See Docs/plans/admin-panel.md.
"""

from fastapi import APIRouter

from app.api.admin.businesses import router as businesses_router
from app.api.admin.audit import router as audit_router

router = APIRouter()
router.include_router(businesses_router, prefix="/businesses", tags=["admin-businesses"])
router.include_router(audit_router, prefix="/audit", tags=["admin-audit"])
