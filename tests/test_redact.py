"""Tests for masking sensitive values."""

import pytest

from tagtrace.redact import Redactor


@pytest.mark.parametrize(
    ("text", "secret"),
    [
        ("mail jane.doe@example.co.uk now", "jane.doe@example.co.uk"),
        ("server 10.20.30.40 down", "10.20.30.40"),
        ("key ghp_abcdefghijklmnop1234", "ghp_abcdefghijklmnop1234"),
        ("key AKIAABCDEFGHIJKLMNOP", "AKIAABCDEFGHIJKLMNOP"),
        ("Authorization: Bearer abc.def-ghijklmnop", "abc.def-ghijklmnop"),
        ("password=hunter2", "hunter2"),
        ("api_key: xyz123", "xyz123"),
    ],
)
def test_built_in_rules_mask_values(text: str, secret: str) -> None:
    assert secret not in Redactor()(text)


def test_same_value_gets_the_same_placeholder() -> None:
    redact = Redactor()
    assert redact("a@example.com and b@example.com") == "[EMAIL_1] and [EMAIL_2]"
    assert redact("again a@example.com") == "again [EMAIL_1]"


def test_custom_patterns_run() -> None:
    assert Redactor([r"CUST-\d+"])("customer CUST-991") == "customer [CUSTOM_1]"


def test_keys_and_shas_stay_readable() -> None:
    text = "ACT-123 reverts commit 0123456789abcdef0123456789abcdef01234567 in v1.2.3"
    assert Redactor()(text) == text


@pytest.mark.parametrize(
    ("text", "secret"),
    [
        ("GITHUB_TOKEN=abcdef123456", "abcdef123456"),
        ("db_password=hunter2", "hunter2"),
        ("client_secret: abc123", "abc123"),
        ('{"password": "hunter2"}', "hunter2"),
        ("token eyJhbGciOiJI.eyJzdWIiOiIx.abc-_def here", "eyJhbGciOiJI"),
        ("Authorization: Basic dXNlcjpwYXNz", "dXNlcjpwYXNz"),
        ("-----BEGIN RSA PRIVATE KEY-----\nMIIEabc\n-----END RSA PRIVATE KEY-----", "MIIEabc"),
        ("clone https://bob:s3cret@git.internal/repo", "s3cret"),
    ],
)
def test_common_secret_forms_are_masked(text: str, secret: str) -> None:
    assert secret not in Redactor()(text)


def test_url_credentials_mask_the_user_name_too() -> None:
    assert "bob" not in Redactor()("https://bob:s3cret@git.internal/repo")
