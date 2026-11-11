"""Public-content checks shared by repository documentation publishers."""

from __future__ import annotations

import re
from dataclasses import dataclass

_EXEMPTION_MARKER = "public-hygiene: allow"
_PRIVATE_ENVIRONMENT_NAMES = ("ai-harness", "firstmate")  # public-hygiene: allow
_PRIVATE_ENVIRONMENT_PREFIX = "aih"  # public-hygiene: allow
_ENVIRONMENT_NAME = re.compile("|".join(re.escape(name) for name in _PRIVATE_ENVIRONMENT_NAMES), re.IGNORECASE)
_ENVIRONMENT_SESSION_NAME = re.compile(rf"(?:^|[^A-Za-z0-9]){re.escape(_PRIVATE_ENVIRONMENT_PREFIX)}-")
_ENVIRONMENT_PREFIX_LITERAL = re.compile(rf"[\"']{re.escape(_PRIVATE_ENVIRONMENT_PREFIX)}[\"']", re.IGNORECASE)
_PRIVATE_TRACKER_ID = re.compile(r"\b(?:AIH|SW|CORE)-[0-9A-Za-z]{2,}\b")  # public-hygiene: allow


@dataclass(frozen=True)
class PublicationViolations:
    """Safe aggregate counts for one source; offending values are never retained."""

    private_tracker_references: int = 0
    private_environment_references: int = 0

    def __bool__(self) -> bool:
        return bool(self.private_tracker_references or self.private_environment_references)


def line_has_private_environment_name(line: str) -> bool:
    """Return whether a line identifies the private environment this project came from."""
    return bool(
        _ENVIRONMENT_NAME.search(line)
        or _ENVIRONMENT_SESSION_NAME.search(line)
        or _ENVIRONMENT_PREFIX_LITERAL.search(line)
    )


def line_has_private_tracker_id(line: str) -> bool:
    """Return whether a line contains an issue ID from a private tracker."""
    return bool(_PRIVATE_TRACKER_ID.search(line))


def publication_violations(text: str) -> PublicationViolations:
    """Count private references in a source without returning their values."""
    tracker = environment = 0
    for line in text.splitlines():
        if _EXEMPTION_MARKER in line:
            continue
        tracker += line_has_private_tracker_id(line)
        environment += line_has_private_environment_name(line)
    return PublicationViolations(tracker, environment)


def sanitize_machine_receipt(text: str) -> str:
    """Remove private references from a machine receipt without changing its source file."""
    text = _PRIVATE_TRACKER_ID.sub("private-tracker-reference", text)
    text = _ENVIRONMENT_NAME.sub("private-environment", text)
    text = _ENVIRONMENT_SESSION_NAME.sub(lambda match: match.group(0)[:-4] + "example-", text)
    return _ENVIRONMENT_PREFIX_LITERAL.sub(lambda match: f"{match.group(0)[0]}example{match.group(0)[-1]}", text)
