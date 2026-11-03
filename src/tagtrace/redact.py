"""Masks sensitive values in text before it can reach a remote model. It runs on this machine only."""

import re
from collections.abc import Iterable

_BUILT_IN: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("SECRET", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|\Z)", re.DOTALL)),
    ("SECRET", re.compile(r"(?<=://)[^\s/@]+:\S*(?=@)")),
    ("SECRET", re.compile(r"\beyJ[-_A-Za-z0-9]+\.[-_A-Za-z0-9]+\.[-_A-Za-z0-9]*")),
    ("SECRET", re.compile(r"\b(?:ghp|gho|ghs|github_pat|glpat|xox[abprs]|sk|AKIA)[-_A-Za-z0-9]{12,}\b")),
    ("SECRET", re.compile(r"(?i)\bauthorization[\"']?\s*:\s*[\"']?\S+(?:\s+[-._~+/=A-Za-z0-9]+)?")),
    ("SECRET", re.compile(r"(?i)\bbearer\s+[-._~+/A-Za-z0-9]{12,}=*")),
    (
        "SECRET",
        re.compile(r"(?i)[\w.-]*(?:password|passwd|pwd|secret|token|api[-_]?key)[\"']?\s*[=:]\s*[\"']?[^\s,;\"'}]+"),
    ),
    ("EMAIL", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+\b")),
    ("IP", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")),
)


class Redactor:
    """Replaces each sensitive value with a placeholder such as `[EMAIL_1]`.

    The same value always gets the same placeholder, so a model can still see that two mentions are related.
    """

    def __init__(self, patterns: Iterable[str] = ()) -> None:
        """Compile the user's patterns, which run before the built-in ones."""
        self._rules = [("CUSTOM", re.compile(pattern)) for pattern in patterns] + list(_BUILT_IN)
        self._placeholders: dict[str, str] = {}
        self._counts: dict[str, int] = {}

    def __call__(self, text: str) -> str:
        """Return `text` with every sensitive value masked."""
        for label, pattern in self._rules:
            text = pattern.sub(lambda match, label=label: self._placeholder(label, match.group(0)), text)
        return text

    def _placeholder(self, label: str, value: str) -> str:
        """Return the placeholder for a value, creating it on first sight."""
        if value not in self._placeholders:
            self._counts[label] = self._counts.get(label, 0) + 1
            self._placeholders[value] = f"[{label}_{self._counts[label]}]"
        return self._placeholders[value]


def no_redaction(text: str) -> str:
    """Return `text` unchanged, for local models or when redaction is turned off."""
    return text
