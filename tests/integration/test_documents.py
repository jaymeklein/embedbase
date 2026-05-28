"""Integration tests for document endpoints.

All routes return 501 until Delivery 2. These tests pin that contract so a
partial implementation doesn't silently change the status code.
"""


async def _setup(client):
    ws_id = (await client.post("/workspaces", json={"name": "WS"})).json()["id"]
    col_id = (
        await client.post(f"/workspaces/{ws_id}/collections", json={"name": "C"})
    ).json()["id"]
    return ws_id, col_id


async def test_upload_document_returns_501(client):
    ws_id, col_id = await _setup(client)
    r = await client.post(
        f"/workspaces/{ws_id}/collections/{col_id}/documents"
    )
    assert r.status_code == 501


async def test_list_documents_returns_501(client):
    ws_id, col_id = await _setup(client)
    r = await client.get(f"/workspaces/{ws_id}/collections/{col_id}/documents")
    assert r.status_code == 501


async def test_get_document_status_returns_501(client):
    ws_id, col_id = await _setup(client)
    r = await client.get(
        f"/workspaces/{ws_id}/collections/{col_id}/documents/doc_abc/status"
    )
    assert r.status_code == 501


async def test_delete_document_returns_501(client):
    ws_id, col_id = await _setup(client)
    r = await client.delete(
        f"/workspaces/{ws_id}/collections/{col_id}/documents/doc_abc"
    )
    assert r.status_code == 501


async def test_upload_document_flat_alias_returns_501(client):
    r = await client.post("/documents")
    assert r.status_code == 501


async def test_delete_document_flat_alias_returns_501(client):
    r = await client.delete("/documents/doc_abc")
    assert r.status_code == 501


async def test_search_returns_501(client):
    r = await client.post(
        "/search", json={"query": "hello", "collection_ids": ["col_abc"]}
    )
    assert r.status_code == 501
