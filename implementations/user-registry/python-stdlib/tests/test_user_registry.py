"""Contract + property tests for user-registry candidate (invariant lineage: B-001..B-005, S-001, S-002).

Every test names the invariant it witnesses (per pdd-implementation-generator rule).
Run with: python3 -m pytest implementations/user-registry/python-stdlib/tests/
"""

import sys
from pathlib import Path

import hypothesis
from hypothesis import given, settings, strategies as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from user_registry import UserRegistry, _normalize_email  # noqa: E402

VALID_REQ = st.fixed_dictionaries(
    {
        "client_request_id": st.text(min_size=1, max_size=128),
        # Exactly the schema's accepted set (S-001 pattern, ASCII-only with
        # optional whitespace padding), so generators and the protocol agree
        # on what is a valid email.
        "email": st.from_regex(r"^\s*[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\s*$", fullmatch=True).filter(
            lambda s: 3 <= len(s) <= 320),
        "display_name": st.text(min_size=1, max_size=200),
    }
)

INVALID_REQ = st.one_of(
    st.none(),
    st.integers(),
    st.fixed_dictionaries({"client_request_id": st.text(min_size=1, max_size=128)}),  # missing email/display_name
    st.fixed_dictionaries({"client_request_id": st.text(max_size=0), "email": st.text(), "display_name": st.text()}),
    st.fixed_dictionaries(
        {"client_request_id": st.text(min_size=1, max_size=128),
         "email": st.text(min_size=1, max_size=320),
         "display_name": st.text(min_size=1, max_size=200),
         "extra_field": st.text()}),
)


# ---- B-001: idempotent creation --------------------------------------------

@given(VALID_REQ)
@settings(max_examples=200, deadline=None)
def test_B001_repeat_request_id_returns_original_without_second_write(request):
    reg = UserRegistry()
    first = reg.create(request)
    second = reg.create(request)
    assert first["ok"] is True and second["ok"] is True
    assert second["outcome"] == "existing"
    assert second["user"] == first["user"]
    assert len(reg) == 1  # one committed write total


@given(st.lists(VALID_REQ, min_size=1, max_size=50))
@settings(max_examples=50, deadline=None)
def test_B001_many_repeats_never_grow_state(requests):
    reg = UserRegistry()
    for request in requests:
        reg.create(request)
    size_before = len(reg)
    for request in requests:
        reg.create(request)
        reg.create(request)
    assert len(reg) == size_before  # repeats never add users


# ---- B-002: email uniqueness ------------------------------------------------

@given(VALID_REQ, VALID_REQ)
@settings(max_examples=100, deadline=None)
def test_B002_different_ids_same_email_second_conflicts(request_a, request_b):
    hypothesis.assume(request_a["email"] != request_b["email"]
                      or request_a["client_request_id"] != request_b["client_request_id"])
    reg = UserRegistry()
    r1 = reg.create(request_a)
    r2 = reg.create({**request_b, "email": request_a["email"]})
    if request_a["client_request_id"] == request_b["client_request_id"]:
        assert r2["outcome"] == "existing"  # same id -> idempotent, not conflict
    else:
        assert r1["ok"] is True
        assert r2["ok"] is False
        assert r2["error"]["kind"] == "conflict"
        assert len(reg) == 1  # second create performed no write


# ---- B-003: invalid input fails closed --------------------------------------

@given(INVALID_REQ)
@settings(max_examples=200, deadline=None)
def test_B003_invalid_input_fails_closed_no_state_change(request):
    reg = UserRegistry()
    result = reg.create(request)
    assert result["ok"] is False
    assert result["error"]["kind"] == "invalid_request"
    assert len(reg) == 0  # no state change


@given(VALID_REQ, INVALID_REQ)
@settings(max_examples=100, deadline=None)
def test_B003_invalid_after_valid_keeps_prior_state(request, bad):
    reg = UserRegistry()
    reg.create(request)
    size_before = len(reg)
    reg.create(bad)
    assert len(reg) == size_before


# ---- B-004: deterministic reads ----------------------------------------------

@given(VALID_REQ)
@settings(max_examples=100, deadline=None)
def test_B004_get_is_deterministic_and_non_mutating(request):
    reg = UserRegistry()
    reg.create(request)
    size_before = len(reg)
    a = reg.get(request["client_request_id"])
    b = reg.get(request["client_request_id"])
    assert a == b
    assert len(reg) == size_before  # reads never write


def test_B004_get_unknown_id_returns_not_found():
    reg = UserRegistry()
    result = reg.get("does-not-exist")
    assert result["ok"] is False
    assert result["error"]["kind"] == "not_found"


# ---- B-005: email normalization ----------------------------------------------

@given(VALID_REQ)
@settings(max_examples=100, deadline=None)
def test_B005_case_and_whitespace_variants_conflict(request):
    hypothesis.assume(len(request["client_request_id"]) < 100)  # variant id needs headroom
    email = request["email"]
    variant = {"client_request_id": request["client_request_id"] + "-x",
               "email": "  " + email.upper() + "  ",
               "display_name": request["display_name"]}
    reg = UserRegistry()
    first = reg.create(request)
    second = reg.create(variant)
    assert first["ok"] is True
    assert second["ok"] is False
    assert second["error"]["kind"] == "conflict"
    assert len(reg) == 1
    # stored email is the normalized form
    assert first["user"]["email"] == _normalize_email(email)


# ---- S-001 / S-002: schema & envelope contract --------------------------------

def test_S001_created_user_matches_response_schema_shape():
    reg = UserRegistry()
    result = reg.create({"client_request_id": "r1", "email": "a@b.com", "display_name": "A"})
    user = result["user"]
    for field in ("id", "client_request_id", "email", "display_name", "created_at"):
        assert field in user and isinstance(user[field], str) and user[field]
    assert result["ok"] is True


def test_S002_error_envelope_uses_enumerated_kinds():
    reg = UserRegistry()
    results = [
        reg.create({"client_request_id": "r", "email": "not-an-email", "display_name": "x"}),
        reg.create({"client_request_id": "r2", "email": "a@b.com", "display_name": "x"}),
        reg.create({"client_request_id": "r3", "email": "a@b.com", "display_name": "x"}),
        reg.get("nope"),
    ]
    kinds = {r["error"]["kind"] for r in results if not r["ok"]}
    assert kinds == {"invalid_request", "conflict", "not_found"}
    for r in results:
        if not r["ok"]:
            assert isinstance(r["error"]["message"], str) and r["error"]["message"]
