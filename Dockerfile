# Docker-based build path for the pdd service image (fallback to the Nix
# pipeline: flake.nix `.#image` + nix2container — both layouts ship the same
# repo content at /opt/pdd; toolchain versions are pinned exactly here, and
# float to the nixpkgs-25.05 channel in the flake).
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PORT=8080

WORKDIR /opt/pdd

RUN pip install --no-cache-dir \
    pytest==9.0.3 \
    hypothesis==6.165.0 \
    jsonschema==4.26.0 \
    pyyaml==6.0.3 \
    psycopg[binary]==3.2.9

COPY src/ src/
COPY scripts/ scripts/
COPY validators/ validators/
COPY pdd-bundles/ pdd-bundles/
COPY implementations/ implementations/
COPY evidence/ evidence/
# Only the pdd-repository skills ship in the image (the runtime server
# subprocesses pdd-evidence-keeper/scripts/evidence_chain.py, and the future
# MCP server serves the pdd-* skills as resources). .reasonix/ also holds
# agent-harness files (desktop-topic-*.json, tasks/) that are NOT part of
# this project and must not be baked into the artifact.
COPY .reasonix/skills/ .reasonix/skills/
COPY Makefile README.md ./

EXPOSE 8080

# Keep the image usable both as the service and as an exec-able toolchain:
# the default command is the HTTP service; `docker run ... python3 scripts/pdd.py ...`
# (or kubectl exec) reaches the full pdd CLI.
CMD ["python3", "src/server.py"]
