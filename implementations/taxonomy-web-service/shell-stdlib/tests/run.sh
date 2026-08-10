#!/bin/sh
# Behavioral tests for the shell-stdlib candidate — exit 0 = all pass.
set -u
fail=0
check() {
    desc="$1"; want="$2"; got="$3"
    if [ "$got" = "$want" ]; then
        echo "PASS $desc"
    else
        echo "FAIL $desc (want '$want', got '$got')"
        fail=1
    fi
}

# B-001: known components pass
out=$(sh validate.sh __run__ "api=x database=y" 2>&1); rc=$?
check "B001_known_components_pass" "0" "$rc"

# B-001: unknown component reported
out=$(sh validate.sh __run__ "api=x nosuchcomp=y" 2>&1); rc=$?
check "B001_unknown_component_reported" "1" "$rc"
echo "$out" | grep -q "unknown-component:nosuchcomp" || { echo "FAIL B001_unknown_component_reported text"; fail=1; }

# B-001: unknown template reference reported
out=$(sh validate.sh __run__ "api=template:nosuchtmpl" 2>&1); rc=$?
check "B001_unknown_template_ref_reported" "1" "$rc"
echo "$out" | grep -q "unknown-template:nosuchtmpl" || { echo "FAIL B001_unknown_template_ref_reported text"; fail=1; }

# B-001: empty input passes (nothing to validate)
out=$(sh validate.sh __run__ "" 2>&1); rc=$?
check "B001_empty_components_pass" "0" "$rc"

# B-001: full sealed vocabulary passes
out=$(sh validate.sh __run__ "ingress=x api=x authn=x authorization=x database=x cache=x queue=x storage=x observability=x config=x scheduler=x worker=x" 2>&1); rc=$?
check "B001_full_vocabulary_pass" "0" "$rc"

if [ "$fail" -eq 0 ]; then
    echo "5 passed, 0 failed"
    exit 0
fi
echo "FAILURES"
exit 1
