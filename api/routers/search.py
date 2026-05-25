from fastapi import APIRouter, HTTPException

from api.models.search import SearchRequest, SearchResponse

router = APIRouter(tags=["search"])


@router.post("/search", response_model=SearchResponse)
async def search(request: SearchRequest):
    # Full implementation in Delivery 3
    raise HTTPException(501, "Search implemented in Delivery 3")
