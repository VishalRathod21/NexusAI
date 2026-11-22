"""Start the whole NexusAI stack locally: 3 MCP servers, 4 A2A agents, the
backend and (optionally) the React dashboard. Each one is a separate process, which
is the point: agents and tools are independent services.

    python scripts/run_all.py              # everything, including the UI
    python scripts/run_all.py --no-ui      # services only
    python scripts/run_all.py --fixture    # offline web corpus instead of live search

Output of every process goes to logs/<name>.log. Stop with Ctrl+C.
"""

from __future__ import annotations

import argparse
import os
import secrets
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from backend.config.settings import get_settings  # noqa: E402


def port_of(url: str) -> int:
    return urlparse(url).port or 80


def wait_for_port(port: int, timeout: float = 40.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket() as sock:
            sock.settimeout(0.5)
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.3)
    return False


UI_PORT = 5173
FRONTEND = ROOT / "frontend"


def prepare_frontend() -> list[str] | None:
    """Command for the Vite dev server. Installs node packages on first run."""
    node, npm = shutil.which("node"), shutil.which("npm")
    if not node or not npm:
        print("  Node.js is required for the dashboard (https://nodejs.org). "
              "Install it, or run with --no-ui.")
        return None
    vite = FRONTEND / "node_modules" / "vite" / "bin" / "vite.js"
    if not vite.exists():
        print("  installing frontend packages (first run only)...")
        if subprocess.run([npm, "install", "--no-fund", "--no-audit"], cwd=FRONTEND).returncode != 0:
            print("  npm install failed")
            return None
    # Run Vite with node directly (not through npm), so stopping this process
    # really stops the dev server on Windows.
    return [node, str(vite)]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--no-ui", action="store_true", help="do not start the React dashboard")
    parser.add_argument("--fixture", action="store_true", help="use the offline synthetic web corpus")
    args = parser.parse_args()

    settings = get_settings()
    python = sys.executable
    api_port = port_of(settings.api_base_url)

    # (name, command, port, extra environment)
    services: list[tuple[str, list[str], int, dict[str, str]]] = [
        ("mcp-web", [python, "-m", "protocols.mcp.servers.web_research"], port_of(settings.mcp_web_url), {}),
        ("mcp-business", [python, "-m", "protocols.mcp.servers.business_data"], port_of(settings.mcp_business_url), {}),
        ("mcp-calculator", [python, "-m", "protocols.mcp.servers.market_calculator"], port_of(settings.mcp_calculator_url), {}),
        ("agent-market", [python, "-m", "agents.market_research.server"], port_of(settings.market_agent_url), {}),
        ("agent-competitor", [python, "-m", "agents.competitor.server"], port_of(settings.competitor_agent_url), {}),
        ("agent-customer", [python, "-m", "agents.customer_insights.server"], port_of(settings.customer_agent_url), {}),
        ("agent-strategy", [python, "-m", "agents.strategy.server"], port_of(settings.strategy_agent_url), {}),
        (
            "backend",
            [python, "-m", "uvicorn", "backend.main:app", "--host", "127.0.0.1", "--port", str(api_port)],
            api_port,
            {},
        ),
    ]
    if not args.no_ui:
        frontend = prepare_frontend()
        if frontend is None:
            return 1
        services.append(("frontend", frontend, UI_PORT, {"API_BASE_URL": settings.api_base_url}))

    env = {**os.environ, "PYTHONPATH": str(ROOT), "PYTHONUNBUFFERED": "1"}
    if args.fixture:
        env["WEB_SEARCH_BACKEND"] = "fixture"
    if not settings.internal_token():
        # No token in .env: make one for this launch so the agents and MCP
        # servers only accept calls from this stack. Every process gets it.
        env["INTERNAL_API_TOKEN"] = secrets.token_urlsafe(32)

    logs = ROOT / "logs"
    logs.mkdir(exist_ok=True)
    processes: list[tuple[str, subprocess.Popen, object]] = []
    try:
        for name, command, port, extra in services:
            if name.startswith("mcp-"):
                extra = {**extra, "MCP_PORT": str(port)}
            handle = open(logs / f"{name}.log", "w", encoding="utf-8")
            cwd = FRONTEND if name == "frontend" else ROOT
            process = subprocess.Popen(command, cwd=cwd, env={**env, **extra}, stdout=handle, stderr=subprocess.STDOUT)
            processes.append((name, process, handle))
            ready = wait_for_port(port)
            if process.poll() is not None or not ready:
                print(f"  FAILED  {name:<16} (see logs/{name}.log)")
                return 1
            print(f"  ready   {name:<16} http://127.0.0.1:{port}")

        print(f"\nLLM: {settings.llm_provider}:{settings.resolved_model}   "
              f"web search: {env.get('WEB_SEARCH_BACKEND', settings.web_search_backend)}")
        print(f"API docs:  http://127.0.0.1:{api_port}/docs")
        if not args.no_ui:
            print(f"Dashboard: http://127.0.0.1:{UI_PORT}")
        print("Press Ctrl+C to stop.\n")

        while True:
            time.sleep(1)
            for name, process, _handle in processes:
                if process.poll() is not None:
                    # Kept running on purpose: this is how you observe a failed agent.
                    print(f"  exited  {name} (code {process.returncode}); the rest keep running")
                    processes = [p for p in processes if p[0] != name]
                    break
    except KeyboardInterrupt:
        print("\nstopping...")
    finally:
        for _name, process, handle in processes:
            if process.poll() is None:
                process.terminate()
        for _name, process, handle in processes:
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()
            handle.close()  # type: ignore[attr-defined]
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
