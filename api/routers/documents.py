from fastapi import APIRouter, HTTPException

router = APIRouter(tags=["documents"])


@router.post("/workspaces/{ws_id}/collections/{col_id}/documents", status_code=202)
async def upload_document(ws_id: str, col_id: str):
    # Full implementation in Delivery 2
    raise HTTPException(501, "Document ingestion implemented in Delivery 2")


@router.get("/workspaces/{ws_id}/collections/{col_id}/documents")
async def list_documents(ws_id: str, col_id: str):
    raise HTTPException(501, "Implemented in Delivery 2")


@router.get("/workspaces/{ws_id}/collections/{col_id}/documents/{doc_id}/status")
async def get_document_status(ws_id: str, col_id: str, doc_id: str):
    raise HTTPException(501, "Implemented in Delivery 2")


@router.delete("/workspaces/{ws_id}/collections/{col_id}/documents/{doc_id}", status_code=204)
async def delete_document(ws_id: str, col_id: str, doc_id: str):
    raise HTTPException(501, "Implemented in Delivery 2")


# ── Flat aliases (convenience for MCP and programmatic clients) ───────────────

@router.post("/documents", status_code=202)
async def upload_document_flat():
    raise HTTPException(501, "Flat alias — implemented in Delivery 2")


@router.delete("/documents/{doc_id}", status_code=204)
async def delete_document_flat(doc_id: str):
    raise HTTPException(501, "Flat alias — implemented in Delivery 2")
