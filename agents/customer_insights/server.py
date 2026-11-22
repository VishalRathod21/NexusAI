"""A2A service for the Customer Insights Agent.

Run:  python -m agents.customer_insights.server
"""

from backend.config.settings import get_settings

from agents.customer_insights.agent import CustomerInsightsAgent
from agents.a2a_server import build_agent_app, serve

PUBLIC_URL = get_settings().customer_agent_url
app = build_agent_app(CustomerInsightsAgent(), PUBLIC_URL)

if __name__ == "__main__":
    serve(app, PUBLIC_URL)
