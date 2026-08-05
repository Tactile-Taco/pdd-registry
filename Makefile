.PHONY: lint test validate evidence all ci-install seal

PY ?= python3

## Lint every protocol bundle with the hardened linter
lint:
	$(PY) scripts/pdd.py bundle lint

## Run the candidate test suites with a scrubbed environment: candidate code
## under pytest must NEVER see PDD_EVIDENCE_KEY or other caller secrets, and
## gets a fresh temp HOME so it cannot read the invoking user's private files.
test:
	env -i PATH="$$PATH" HOME="$$(mktemp -d)" LANG="C.UTF-8" PBT_RUNS=200 $(PY) -m pytest implementations/ -q

## Run the full three-layer Validator Loop on the sealed bundle's candidate
## (candidate execution is env-scrubbed inside validate_candidate.py)
validate:
	$(PY) scripts/pdd.py validate user-registry --pbt-runs 200

## Build the signed Evidence Chain + genesis ledger block, then verify the
## ledger and every admission evidence object (needs PDD_EVIDENCE_KEY exported)
evidence: 
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
