FROM python:3.12-slim

# Install system dependencies & Node.js 22
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    git \
    build-essential \
    ca-certificates \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && npm install -g @anthropic-ai/claude-code @openai/codex @google/gemini-cli \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy application code
COPY . /app

# Install GhostPilot package
RUN pip install --no-cache-dir -e .

# Expose internal control plane port
EXPOSE 8765

CMD ["python", "-m", "ghostpilot.main"]
