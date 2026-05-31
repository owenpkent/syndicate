# Single base image for every agent and pipeline. Each service overrides the
# command with its console entrypoint (sportsball-oracle, -engine, etc.), so we
# build one image instead of maintaining five near-identical Dockerfiles.
FROM python:3.11-slim

WORKDIR /app

# Install dependencies first (cached) using just the project metadata.
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

# Strategy params and models are mounted at runtime (see docker-compose.yml);
# config/ is mounted so settings.json changes don't require a rebuild.

# Default role; overridden per service.
CMD ["sportsball-engine"]
