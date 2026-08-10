#!/bin/sh
# taxonomy-web-service shell-stdlib candidate — vocabulary validator in POSIX sh.
# Demonstrates the language-agnostic candidate harness: the candidate's
# behavioral layer runs the manifest-declared test_command
# ["sh", "tests/run.sh"] instead of pytest; the Python-shaped layers
# (O-003 AST scan, sandbox smoke, benchmark, mutant harness) are honestly
# skipped for language != python.
#
# validate_against <components-json-ish> — exit 0 if every component name is
# in the sealed vocabulary and every template reference resolves; prints one
# violation per line to stdout, mirrors the python-stdlib variant contract.

set -u

# Sealed vocabulary (mirrors pdd-bundles/taxonomy-web-service capabilities.components)
VOCABULARY="ingress api authn authorization database cache queue storage observability config scheduler worker"

validate_against() {
    components="$1"
    violations=""
    for entry in $components; do
        name="${entry%%=*}"
        val="${entry#*=}"
        case "$val" in
            template:*) tmpl="${val#template:}"
                found=0
                for known in $VOCABULARY; do
                    if [ "$tmpl" = "$known" ]; then found=1; fi
                done
                if [ "$found" -eq 0 ]; then
                    violations="$violations unknown-template:$tmpl (component $name)
"
                fi ;;
        esac
        found=0
        for known in $VOCABULARY; do
            if [ "$name" = "$known" ]; then found=1; fi
        done
        if [ "$found" -eq 0 ]; then
            violations="$violations unknown-component:$name
"
        fi
    done
    if [ -n "$violations" ]; then
        printf '%s' "$violations"
        return 1
    fi
    return 0
}

# CLI: sh validate.sh "api=x database=y" (test runner invokes via the module)
if [ "${1:-}" = "__run__" ]; then
    shift
    validate_against "$1"
fi
