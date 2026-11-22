"""What a failed git or Dolt remote call printed: a refused credential, a lost connection, or neither.

A retry policy retries only what asking again can cure. A lost connection or a remote that
was never reached can answer next time; a refused credential is a definite answer, and so
is anything else the transport reports, such as a missing repository or a bad refspec.
"""

from __future__ import annotations

import re

# What git and its credential helpers print when the remote refuses, or cannot be given, a
# credential. A refusal wins over any connection pattern in the same output.
_AUTH_REFUSAL = re.compile(
    r"could not read (?:Username|Password)|Authentication failed|Permission denied \(publickey"
    r"|HTTP Basic: Access denied|Invalid username or password|returned error: 40[13]",
    re.IGNORECASE,
)
# A remote that was not reached or dropped the transfer. "Could not read from remote
# repository" is not here: git prints it for a repository that does not exist too, which
# is a definite answer.
_CONNECTION_FAILURE = re.compile(
    r"Could not resolve host|Could not resolve hostname|Connection (?:timed out|refused|reset|closed by)"
    r"|ssh: connect to host|Failed to connect|Operation timed out|early EOF|remote end hung up unexpectedly"
    r"|RPC failed|returned error: 5\d\d|Network is unreachable"
    r"|TLS connection was non-properly terminated|SSL_ERROR|unexpected disconnect",
    re.IGNORECASE,
)


def auth_refusal(stderr: str) -> str | None:
    """The line naming a refused credential, or None when the remote refused none."""
    lines = [line.strip() for line in stderr.splitlines() if line.strip()]
    if not _AUTH_REFUSAL.search(stderr):
        return None
    return next((line for line in reversed(lines) if _AUTH_REFUSAL.search(line)), lines[-1])


def is_transient(stderr: str) -> bool:
    """Whether the call lost its connection, so asking again can succeed; never for a refused credential."""
    return auth_refusal(stderr) is None and bool(_CONNECTION_FAILURE.search(stderr))
