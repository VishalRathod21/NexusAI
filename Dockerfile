# One image for every Python service. docker-compose.yml starts it with a
# different command per service (MCP server, agent, backend). The React
# dashboard has its own image: frontend/Dockerfile.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app

# Dependencies first: this layer is cached until requirements.txt changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Do not run as root inside the container.
RUN useradd --create-home nexus && mkdir -p /app/logs && chown -R nexus /app
USER nexus

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
