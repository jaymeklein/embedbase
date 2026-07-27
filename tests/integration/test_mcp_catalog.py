"""The /mcp-tools catalogue endpoint that drives the Settings → MCP page and its generated SKILL.md.

The catalogue is introspected live from the registered MCP tools, so these tests pin the drift
guard: whatever the server registers is exactly what the page and SKILL.md advertise.
"""

from api.services.mcp.server import build_mcp_server


async def test_mcp_tools_lists_grouped_catalog(master_client):
    r = await master_client.get("/mcp-tools")
    assert r.status_code == 200
    groups = r.json()["groups"]
    assert [g["group"] for g in groups] == [
        "Search & read",
        "Documents",
        "Workspaces & collections",
        "Tags",
        "Ops",
    ]
    by_name = {t["name"]: t for g in groups for t in g["tools"]}
    # Signatures are synthesized from each tool's input schema (params, defaults, required).
    assert (
        by_name["search_documents"]["signature"]
        == "(query, collection_ids, top_k=5, hybrid=true, filters?)"
    )
    assert by_name["assign_tag"]["signature"].startswith(
        "(workspace_id, tag_id, target=workspace,"
    )
    # Summary is the first paragraph of the tool docstring, collapsed to one line.
    assert by_name["list_workspaces"]["summary"].startswith("List all workspaces")


async def test_mcp_tools_covers_every_registered_tool(master_client):
    """The catalogue must list exactly the tools the live server registers — the guard that keeps
    the Settings page / SKILL.md from drifting as tools are added, removed, or renamed."""
    r = await master_client.get("/mcp-tools")
    catalog_names = {t["name"] for g in r.json()["groups"] for t in g["tools"]}
    live = await build_mcp_server().list_tools()
    assert catalog_names == {t.name for t in live}


async def test_mcp_tools_requires_auth(client):
    r = await client.get("/mcp-tools")
    assert r.status_code == 401
