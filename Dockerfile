# skillgraph in a container: agent runs and fixture scripts execute here, not on your machine.
#
#   docker build -t skillgraph .
#   docker run --rm -it -e ANTHROPIC_API_KEY -v "$PWD":/work skillgraph doctor
#   docker run --rm -it -e ANTHROPIC_API_KEY -v "$PWD":/work skillgraph pipeline evals/x/suite.yaml --out runs/x
#
# Inside the container only API-key auth works (ANTHROPIC_API_KEY or OPENROUTER_API_KEY);
# your Claude Code login on the host isn't visible here, which is the point.
FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home runner

WORKDIR /opt/skillgraph
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
COPY skills ./skills
RUN pip install --no-cache-dir ".[claude]"

USER runner
WORKDIR /work
ENTRYPOINT ["skillgraph"]
CMD ["--help"]
