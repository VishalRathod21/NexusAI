"""A2A service for the Strategy Agent.

Run:  python -m agents.strategy.server
"""

from backend.config.settings import get_settings

from agents.strategy.agent import StrategyAgent
from agents.a2a_server import build_agent_app, serve

PUBLIC_URL = get_settings().strategy_agent_url
app = build_agent_app(StrategyAgent(), PUBLIC_URL)

if __name__ == "__main__":
    serve(app, PUBLIC_URL)
