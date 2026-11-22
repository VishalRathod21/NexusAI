"""A2A service for the Competitor Agent.

Run:  python -m agents.competitor.server
"""

from backend.config.settings import get_settings

from agents.competitor.agent import CompetitorAgent
from agents.a2a_server import build_agent_app, serve

PUBLIC_URL = get_settings().competitor_agent_url
app = build_agent_app(CompetitorAgent(), PUBLIC_URL)

if __name__ == "__main__":
    serve(app, PUBLIC_URL)
