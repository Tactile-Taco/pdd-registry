.PHONY: lint test validate evidence all ci-install seal

PY ?= python3

## Lint every protocol bundle with the hardened linter
lint:
	$(PY) scripts/pdd.py bundle lint

## Run the candidate test suites with a scrubbed environment: candidate code
## under pytest must NEVER see PDD_EVIDENCE_KEY or other caller secrets, and
## gets a fresh temp HOME so it cannot read the invoking user's private files.
## Then run the service verification surface (src/tests) which needs the real
## PDD_EVIDENCE_KEY (the committed evidence is signed with it): export it, e.g.
##   export PDD_EVIDENCE_KEY=$(infisical secrets get PDD_EVIDENCE_KEY --projectId 7a2f10fc-2d47-4008-a817-3f5493dc7476 --env prod --plain --silent)
test:
	env -i PATH="$$PATH" HOME="$$(mktemp -d)" LANG="C.UTF-8" PBT_RUNS=200 $(PY) -m pytest implementations/ -q
	env -i PATH="$$PATH" HOME="$$HOME" LANG="C.UTF-8" PDD_EVIDENCE_KEY="$${PDD_EVIDENCE_KEY:?export PDD_EVIDENCE_KEY (the key the committed evidence is signed with; see README)}" $(PY) -m pytest src/tests -q

## Run the full three-layer Validator Loop on both sealed bundles' candidates
## (candidate execution is env-scrubbed inside validate_candidate.py)
validate:
	$(PY) scripts/pdd.py validate user-registry --pbt-runs 200
	$(PY) scripts/pdd.py validate pdd-registry --impl implementations/pdd-registry/python-stdlib --pbt-runs 200

## Build the signed Evidence Chain + genesis ledger blocks, then verify the
## ledgers and every admission evidence object for BOTH registries
## (needs PDD_EVIDENCE_KEY exported)
evidence: 
	$(PY) scripts/pdd.py evidence build pdd-registry --impl implementations/pdd-registry/python-stdlib
	$(PY) scripts/pdd.py evidence verify pdd-registry
	$(PY) scripts/pdd.py evidence build user-registry --impl implementations/user-registry/python-stdlib
	$(PY) scripts/pdd.py evidence verify user-registry

## Everything a commit must pass
all: lint test validate evidence

## Install CI workflows into .github/workflows (needs a credential with workflow scope)
ci-install:
	mkdir -p .github/workflows
	cp ci-templates/*.yml .github/workflows/

## Seal a bundle (lint must pass first): make seal NAME=user-registry
seal:
	$(PY) scripts/pdd.py bundle seal $(NAME)
