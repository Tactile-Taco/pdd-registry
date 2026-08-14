.PHONY: install lint test validate evidence all ci-install seal

# The pdd CLI is the shared loop tooling (repo: Tactile-Taco/pdd-cli).
PY ?= .venv/bin/python
PDD ?= .venv/bin/pdd
BUNDLE = pdd-bundles/user-registry
IMPL = implementations/user-registry/python-stdlib
# Where the validator loop ran (CI run URL, or urn: for local runs).
VALIDATION_RESOURCE ?= urn:pdd-registry:validation:local

## Local dev: venv with the loop tooling (CI installs pdd-cli from git).
install:
	python3 -m venv .venv
	.venv/bin/pip install -e ../pdd-cli pytest==9.0.3 hypothesis==6.165.0 \
		jsonschema==4.26.0 pyyaml==6.0.3

## Lint every protocol bundle with the hardened linter (pdd workflow lint)
lint:
	$(PDD) workflow lint

## Run the candidate test suites with a scrubbed environment: candidate code
## under pytest must NEVER see PDD_EVIDENCE_KEY or other caller secrets, and
## gets a fresh temp HOME so it cannot read the invoking user's private files.
## Then run the service verification surface (src/tests) which needs the real
## PDD_EVIDENCE_KEY (the committed evidence is signed with it): export it, e.g.
##   export PDD_EVIDENCE_KEY=$(infisical secrets get PDD_EVIDENCE_KEY --projectId 7a2f10fc-2d47-4008-a817-3f5493dc7476 --env prod --plain --silent)
test:
	env -i PATH="$$PATH" HOME="$$(mktemp -d)" LANG="C.UTF-8" PBT_RUNS=200 $(PY) -m pytest implementations/ -q
	env -i PATH="$$PATH" HOME="$$HOME" LANG="C.UTF-8" PDD_EVIDENCE_KEY="$${PDD_EVIDENCE_KEY:?export PDD_EVIDENCE_KEY (the key the committed evidence is signed with; see README)}" $(PY) -m pytest src/tests -q

## Run the full three-layer Validator Loop on the sealed bundle's candidate
## (candidate execution is env-scrubbed inside the engine)
validate:
	$(PDD) workflow validate $(BUNDLE) --impl $(IMPL) --pbt-runs 200

## Build the signed Evidence Chain + genesis ledger block, then verify the
## ledger and every admission evidence object (needs PDD_EVIDENCE_KEY exported).
## The staleness gate covers the bundles THIS repo's loop owns (user-registry,
## pdd-registry); the fleet bundles are catalog entries validated in their own
## repos (see pdd-validator-loop.yml) and are not re-attested here.
evidence:
	$(PDD) workflow evidence build $(BUNDLE) --impl $(IMPL) \
		--validation-resource $(VALIDATION_RESOURCE)
	$(PDD) workflow evidence verify $(BUNDLE)
	$(PDD) workflow staleness $(BUNDLE) pdd-bundles/pdd-registry

## Everything a commit must pass
all: lint test validate evidence

## Install CI workflows into .github/workflows (needs a credential with workflow scope)
ci-install:
	mkdir -p .github/workflows
	cp ci-templates/*.yml .github/workflows/

## Seal a bundle (lint must pass first): make seal NAME=user-registry
seal:
	$(PDD) workflow seal pdd-bundles/$(NAME)
