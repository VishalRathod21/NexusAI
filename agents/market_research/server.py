"""A2A service for the Market Research Agent.

Run:  python -m agents.market_research.server
"""

from backend.config.settings import get_settings

from agents.market_research.agent import MarketResearchAgent
from agents.a2a_server import build_agent_app, serve

PUBLIC_URL = get_settings().market_agent_url
app = build_agent_app(MarketResearchAgent(), PUBLIC_URL)

if __name__ == "__main__":
    serve(app, PUBLIC_URL)
