"""What a failed git or Dolt remote call printed: a refused credential, a lost connection, or neither.

A retry policy retries only what asking again can cure. A lost connection or a remote that
was never reached can answer next time; a refused credential is a definite answer, and so
is anything else the transport reports, such as a missing repository or a bad refspec.

The transient marker, a contract any credential helper can follow. A helper's exit status
does not survive git's credential-helper protocol: when a helper cannot answer, git only
goes on to prompt, and with prompts disabled it prints "could not read Username for ...".
That line reads the same whether the helper was refused a login or merely could not reach
its own identity service for a moment. A helper says it was the second by printing a stderr
line of the form::

    <helper-name>: transient: <cause>

where ``<helper-name>`` is letters, digits, ``_``, ``.`` or ``-``. Such a line marks the failure
transient, so it is retried, and it outranks git's follow-on "could not read Username" or
"could not read Password". It never outranks a refusal the remote itself gave:
"Authentication failed", an HTTP 401 or 403, or a rejected ssh key stops at once.
"""

from __future__ import annotations

import re

# A credential helper's own report that it could not answer this time (see the module docstring).
_TRANSIENT_MARKER = re.compile(r"^\s*[A-Za-z0-9_.-]+: transient:.*$", re.MULTILINE)
# A refusal the remote itself gave: a definite answer whatever any helper printed.
_REMOTE_REFUSAL = re.compile(
    r"Authentication failed|Permission denied \(publickey|HTTP Basic: Access denied"
    r"|Invalid username or password|returned error: 40[13]",
    re.IGNORECASE,
)
# git found no credential to send and could not prompt for one. A refusal, unless a helper
# marked its own failure transient.
_NO_CREDENTIAL = re.compile(r"could not read (?:Username|Password)", re.IGNORECASE)
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


def transient_marker(stderr: str) -> str | None:
    """A credential helper's ``<name>: transient: <cause>`` line, or None when no helper printed one."""
    match = _TRANSIENT_MARKER.search(stderr)
    return match.group(0).strip() if match else None


def auth_refusal(stderr: str) -> str | None:
    """The line naming a refused credential, or None when the remote refused none."""
    lines = [line.strip() for line in stderr.splitlines() if line.strip()]
    remote = next((line for line in reversed(lines) if _REMOTE_REFUSAL.search(line)), None)
    if remote is not None:
        return remote
    if transient_marker(stderr) is not None:
        return None
    return next((line for line in reversed(lines) if _NO_CREDENTIAL.search(line)), None)


def is_transient(stderr: str) -> bool:
    """Whether asking again can succeed: a helper's transient marker or a lost connection, never a refusal."""
    if auth_refusal(stderr) is not None:
        return False
    return transient_marker(stderr) is not None or bool(_CONNECTION_FAILURE.search(stderr))
