"""Market Calculator MCP server.

LLMs are unreliable at arithmetic, so numbers in a report are computed here.
Every tool returns the formula and the intermediate steps along with the
result, so a reader can check the calculation.

Run:  python -m protocols.mcp.servers.market_calculator
"""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import Field

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

mcp = MCPServer(
    "nexusai-market-calculator",
    instructions="Transparent business arithmetic. Results include formula and steps.",
)

Number = Annotated[float, Field(ge=-1e15, le=1e15)]
Positive = Annotated[float, Field(gt=0, le=1e15)]
Percent = Annotated[float, Field(ge=0, le=100)]


@mcp.tool()
def percentage_change(old_value: Number, new_value: Number) -> dict[str, Any]:
    """Percentage change from old_value to new_value."""
    if old_value == 0:
        raise ToolError("old_value must not be zero")
    change = (new_value - old_value) / abs(old_value) * 100
    return {
        "result_pct": round(change, 2),
        "formula": "(new_value - old_value) / |old_value| * 100",
        "steps": [
            f"difference = {new_value} - {old_value} = {new_value - old_value}",
            f"{new_value - old_value} / {abs(old_value)} * 100 = {round(change, 2)}",
        ],
    }


@mcp.tool()
def cagr(
    start_value: Positive,
    end_value: Positive,
    years: Annotated[float, Field(gt=0, le=100)],
) -> dict[str, Any]:
    """Compound annual growth rate between two values."""
    rate = ((end_value / start_value) ** (1 / years) - 1) * 100
    return {
        "result_pct": round(rate, 2),
        "formula": "((end_value / start_value) ^ (1 / years) - 1) * 100",
        "steps": [
            f"ratio = {end_value} / {start_value} = {round(end_value / start_value, 6)}",
            f"ratio ^ (1 / {years}) - 1 = {round(rate / 100, 6)}",
        ],
    }


@mcp.tool()
def market_size_top_down(
    total_addressable_units: Positive,
    target_share_pct: Percent,
    adoption_pct: Percent,
    annual_revenue_per_unit: Annotated[float, Field(ge=0, le=1e12)],
    currency: Annotated[str, Field(max_length=8)] = "USD",
) -> dict[str, Any]:
    """Top-down market size: TAM, SAM and SOM from a base count and percentages.

    TAM = units * revenue per unit
    SAM = TAM * target_share_pct   (the part you can actually serve)
    SOM = SAM * adoption_pct       (the part you can realistically win)
    """
    tam = total_addressable_units * annual_revenue_per_unit
    sam = tam * target_share_pct / 100
    som = sam * adoption_pct / 100
    return {
        "currency": currency,
        "tam": round(tam, 2),
        "sam": round(sam, 2),
        "som": round(som, 2),
        "formula": "TAM = units * revenue_per_unit; SAM = TAM * share%; SOM = SAM * adoption%",
        "steps": [
            f"TAM = {total_addressable_units} * {annual_revenue_per_unit} = {round(tam, 2)}",
            f"SAM = {round(tam, 2)} * {target_share_pct}% = {round(sam, 2)}",
            f"SOM = {round(sam, 2)} * {adoption_pct}% = {round(som, 2)}",
        ],
    }


@mcp.tool()
def compare_values(
    label_a: Annotated[str, Field(max_length=80)],
    value_a: Number,
    label_b: Annotated[str, Field(max_length=80)],
    value_b: Number,
) -> dict[str, Any]:
    """Compare two numbers: difference, ratio and which is larger."""
    difference = value_a - value_b
    ratio = round(value_a / value_b, 4) if value_b != 0 else None
    larger = label_a if value_a > value_b else label_b if value_b > value_a else "equal"
    return {
        "larger": larger,
        "difference": round(difference, 4),
        "ratio_a_to_b": ratio,
        "formula": "difference = value_a - value_b; ratio = value_a / value_b",
        "steps": [f"{label_a} = {value_a}", f"{label_b} = {value_b}"],
    }


def main() -> None:
    from protocols.mcp.servers.runner import run_server

    run_server(mcp, default_port=9103)


if __name__ == "__main__":
    main()
