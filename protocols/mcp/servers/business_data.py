"""Business Data MCP server.

Exposes a small curated company dataset. This server deliberately shows all
three MCP primitives:

* Tools     - actions the model/agent invokes with arguments
              (search_companies, get_company_profile, list_categories)
* Resources - read-only context addressed by URI, loaded by the application
              (business://dataset/info)
* Prompts   - reusable prompt templates a user or app can pick
              (competitor_comparison)

Run:  python -m protocols.mcp.servers.business_data
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any

from pydantic import Field

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

mcp = MCPServer(
    "nexusai-business-data",
    instructions=(
        "Curated company snapshot for competitor research. Coarse facts only; "
        "entries should be verified against the company's own website."
    ),
)


@lru_cache
def _dataset() -> dict[str, Any]:
    path = Path(__file__).parent / "data" / "companies.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _companies() -> list[dict[str, Any]]:
    return _dataset()["companies"]


@mcp.tool()
def list_categories() -> dict[str, Any]:
    """List the categories and regions available in the dataset."""
    companies = _companies()
    return {
        "categories": sorted({c["category"] for c in companies}),
        "regions": sorted({c["region"] for c in companies}),
        "company_count": len(companies),
    }


@mcp.tool()
def search_companies(
    query: Annotated[str, Field(max_length=200, description="Free-text keywords")] = "",
    category: Annotated[str, Field(max_length=50, description="e.g. hr_tech")] = "",
    region: Annotated[str, Field(max_length=50, description="e.g. india or global")] = "",
    limit: Annotated[int, Field(ge=1, le=20)] = 8,
) -> dict[str, Any]:
    """Find companies by keywords, category and region."""
    words = [w for w in query.lower().split() if len(w) > 2]
    matches = []
    for company in _companies():
        if category and company["category"] != category.lower():
            continue
        if region and company["region"] != region.lower():
            continue
        haystack = " ".join(
            str(company.get(k, "")) for k in ("name", "positioning", "target_customers", "headquarters")
        ).lower()
        score = sum(1 for w in words if w in haystack)
        if words and not score and not (category or region):
            continue
        matches.append((score, company))
    matches.sort(key=lambda item: item[0], reverse=True)
    return {
        "companies": [c for _score, c in matches[:limit]],
        "dataset_note": _dataset()["_note"],
    }


@mcp.tool()
def get_company_profile(
    name: Annotated[str, Field(min_length=2, max_length=100, description="Company name")],
) -> dict[str, Any]:
    """Return the dataset entry for one company."""
    for company in _companies():
        if company["name"].lower() == name.strip().lower():
            return {"company": company, "dataset_note": _dataset()["_note"]}
    raise ToolError(f"no company named '{name}' in the dataset")


@mcp.resource("business://dataset/info")
def dataset_info() -> str:
    """Describes the dataset: fields, coverage and caveats."""
    data = _dataset()
    return json.dumps(
        {
            "note": data["_note"],
            "as_of": data["as_of"],
            "fields": sorted(data["companies"][0].keys()),
            "company_count": len(data["companies"]),
        },
        indent=2,
    )


@mcp.prompt()
def competitor_comparison(company_a: str, company_b: str) -> str:
    """Template for comparing two companies from the dataset."""
    return (
        f"Compare {company_a} and {company_b} using only their dataset entries. "
        "Cover positioning, target customers and likely differentiators. "
        "Mark anything not in the dataset as an assumption."
    )


def main() -> None:
    from protocols.mcp.servers.runner import run_server

    run_server(mcp, default_port=9102)


if __name__ == "__main__":
    main()
