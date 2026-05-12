"""Unit tests for :class:`DefaultSecretRedactor`."""

from __future__ import annotations

from helix_agent.runtime.audit import REPLACEMENT, DefaultSecretRedactor


def test_passes_through_when_no_secrets() -> None:
    redactor = DefaultSecretRedactor()
    result = redactor.redact({"action": "manifest:write", "lines_added": 42})

    assert result.redacted == {"action": "manifest:write", "lines_added": 42}
    assert result.hits == {}


def test_masks_openai_key() -> None:
    redactor = DefaultSecretRedactor()
    result = redactor.redact({"prompt": "use key sk-ABCDEFGHIJKLMNOPQRSTUVWX for this"})

    assert REPLACEMENT in result.redacted["prompt"]
    assert "sk-ABCDEFGHIJKLMNOPQRSTUVWX" not in result.redacted["prompt"]
    assert result.hits == {"openai_key": 1}


def test_masks_jwt_three_segment() -> None:
    redactor = DefaultSecretRedactor()
    jwt = (
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1c2VyIiwibmFtZSI6IkFsaWNlIn0"
        ".dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
    )
    result = redactor.redact({"authorization": f"Bearer {jwt}"})

    assert jwt not in result.redacted["authorization"]
    assert result.hits == {"jwt": 1}


def test_masks_bcrypt() -> None:
    redactor = DefaultSecretRedactor()
    bcrypt_hash = "$2a$12$R9h/cIPz0gi.URNNX3kh2OPST9/PgBkqquzi.Ss7KIUgO2t0jWMUW"
    result = redactor.redact({"hashed_password": bcrypt_hash})

    assert bcrypt_hash not in result.redacted["hashed_password"]
    assert result.hits == {"bcrypt": 1}


def test_masks_pem_private_key_header() -> None:
    redactor = DefaultSecretRedactor()
    # Construct the header at runtime so the literal doesn't trip git/pre-commit
    # secret scanners on this test file itself.
    pem_header = "-" * 5 + "BEGIN " + "RSA PRIVATE KEY" + "-" * 5
    pem = pem_header + "\nMIIEogIBA..."
    result = redactor.redact({"key": pem})

    assert pem_header not in result.redacted["key"]
    assert result.hits == {"pem_private_key": 1}


def test_walks_nested_dicts_and_lists() -> None:
    redactor = DefaultSecretRedactor()
    payload = {
        "request": {
            "headers": {"authorization": "Bearer sk-ABCDEFGHIJKLMNOPQRSTUVWX"},
            "args": ["sk-ZZZZZZZZZZZZZZZZZZZZ", 42],
        }
    }
    result = redactor.redact(payload)

    assert (
        "sk-ABCDEFGHIJKLMNOPQRSTUVWX" not in result.redacted["request"]["headers"]["authorization"]
    )
    assert "sk-ZZZZZZZZZZZZZZZZZZZZ" not in result.redacted["request"]["args"][0]
    assert result.redacted["request"]["args"][1] == 42
    assert result.hits["openai_key"] == 2


def test_does_not_mutate_input() -> None:
    redactor = DefaultSecretRedactor()
    original = {"prompt": "sk-ABCDEFGHIJKLMNOPQRSTUVWX"}
    redactor.redact(original)

    # Input must be untouched (immutability rule).
    assert original == {"prompt": "sk-ABCDEFGHIJKLMNOPQRSTUVWX"}


def test_counts_multiple_hits_in_same_string() -> None:
    redactor = DefaultSecretRedactor()
    s = "sk-ABCDEFGHIJKLMNOPQRSTUVWX and sk-ZZZZZZZZZZZZZZZZZZZZ"
    result = redactor.redact({"prompts": [s]})

    assert result.hits == {"openai_key": 2}


def test_anthropic_pat_pattern() -> None:
    redactor = DefaultSecretRedactor()
    pat = "aforge_pat_abcDEF123_xyz"
    result = redactor.redact({"token": pat})

    assert pat not in result.redacted["token"]
    assert result.hits == {"anthropic_pat": 1}
