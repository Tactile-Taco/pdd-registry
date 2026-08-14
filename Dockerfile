# Docker-based build path for the pdd service image (fallback to the Nix
# pipeline: flake.nix `.#image` + nix2container — both layouts ship the same
# repo content at /opt/pdd; toolchain versions are pinned exactly here, and
# float to the nixpkgs-25.05 channel in the flake).
#
# The loop tooling (linter, validation engine, evidence chain, CLI) comes from
# the pdd-cli package — single source of truth; the Makefile and server call
# the installed `pdd` binary. Pinned to the v0.1.0 release tag.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PORT=8080

WORKDIR /opt/pdd

# git is needed to pip-install pdd-cli from the release tag.
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
    pytest==9.0.3 \
    hypothesis==6.165.0 \
    jsonschema==4.26.0 \
    pyyaml==6.0.3 \
    "pdd-cli @ git+https://github.com/Tactile-Taco/pdd-cli.git@v0.2.0"

COPY src/ src/
COPY pdd-bundles/ pdd-bundles/
COPY implementations/ implementations/
COPY evidence/ evidence/
COPY .reasonix/ .reasonix/
COPY Makefile README.md ./

EXPOSE 8080

# Keep the image usable both as the service and as an exec-able toolchain:
# the default command is the HTTP service; `docker run ... pdd workflow ...`
# (or kubectl exec) reaches the full pdd CLI.
CMD ["python3", "src/server.py"]
