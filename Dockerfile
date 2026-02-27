FROM python:3.12-slim

WORKDIR /app

# Install uv for fast dependency management
RUN pip install --no-cache-dir uv

# Copy dependency files first for better layer caching
COPY pyproject.toml uv.lock* ./

# Install dependencies
RUN uv sync --no-dev --no-editable

# Copy source code
COPY src/ src/
COPY config.yaml .

# Create volume mount point for SQLite database
RUN mkdir -p /data

ENV PYTHONUNBUFFERED=1

ENTRYPOINT ["uv", "run", "polymarket-agent"]
CMD ["run", "--db", "/data/polymarket_agent.db"]
