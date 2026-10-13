"""Complete source comparison; local edited snapshots belong to the client."""

from fastapi import APIRouter

from app.api.deps import DbSession
from app.schemas.comparison import ComparisonRequest
from app.services.comparison_service import compare, inspect_conditions

router = APIRouter(prefix="/analysis", tags=["analysis"])


@router.post("/compare")
def compare_sources(body: ComparisonRequest, db: DbSession):
    return compare(db.bind, body)


@router.post("/compare/conditions")
def inspect_source_conditions(body: ComparisonRequest, db: DbSession):
    return inspect_conditions(db.bind, body)
