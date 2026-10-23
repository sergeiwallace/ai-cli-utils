"""The available-name list: download, digest check, static filters, generation record.

Session names are short human first names. The list a session may take one from is
generated, never hand-edited: :func:`generate` downloads two pinned artifacts (a
public-domain first-name list and a spell-checker dictionary), checks each against its
pinned digest, keeps the names whose length is inside the policy's limits, and drops
every common English word, every denylisted name and every entry of the shipped
``non-names.txt``. The result is ``names.txt`` plus a ``generation.json`` recording how
it was built.

The same generator builds two outputs. ``scripts/refresh_session_names.py`` writes the
seed this package ships under ``data/session-names/``, which is what an install with no
shared registry picks from. ``ai sync-names`` writes the shared registry's copy at the
limits that registry's policy sets.

Nothing is written unless both downloads verify: a digest mismatch or a missing archive
member raises :class:`NameListError` before any output file is opened, so a failed
refresh leaves the previous list byte for byte as it was.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import io
import json
import os
import re
import tarfile
import tempfile
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from . import __version__

SCHEMA = 1
DATA_DIR = Path(__file__).parent / "data" / "session-names"
NAMES_FILE = "names.txt"
GENERATION_FILE = "generation.json"
NON_NAMES_FILE = "non-names.txt"
DOWNLOAD_TIMEOUT_S = 60

_NAME_RE = re.compile(r"[a-z]+")

# The policy an install with no shared registry applies, and the one the shipped seed
# is built at. A shared registry publishes its own snapshot of the same shape, and every
# allocation on a host that has one applies that snapshot instead of this.
DEFAULT_POLICY: dict = {
    "schema": 1,
    "publish_timeout_s": 15,
    "publish_attempts": 5,
    "pick_floor": 10,
    "low_water": 25,
    "offline_spares": 3,
    "denylist": [],
    "limits": {"min_name_length": 3, "max_name_length": 4},
    "source": {
        "url": (
            "https://files.pythonhosted.org/packages/44/4e/"
            "f9cb7ef2df0250f4ba3334fbdabaa94f9c88097089763d8e85ada8092f84/names-0.3.0.tar.gz"
        ),
        "digest": "sha256:726e46254f2ed03f1ffb5d941dae3bc67c35123941c29becd02d48d0caa2a671",
        "members": ["names-0.3.0/names/dist.male.first", "names-0.3.0/names/dist.female.first"],
    },
    "confusable": {
        "words_url": "https://registry.npmjs.org/dictionary-en/-/dictionary-en-4.0.0.tgz",
        "words_digest": (
            "sha512-3NHnE1uq33ZE/CIwaZ6gqxa4BnglHnxeAcTM0GJ7cmtRGcvX9InMK/IqLtYcUMFCUpUNgybH+DzkqdgAo3F1zg=="
        ),
        "words_member": "package/index.dic",
        "max_edit_distance": 1,
        "extra_words": [],
        "keep_names": [],
    },
}

# The per-machine tables of a fleet policy file. Everything else in the file is
# fleet-wide and belongs to the published snapshot.
PER_MACHINE_KEYS = frozenset({"hosts", "defaults"})


class NameListError(RuntimeError):
    """A generation could not complete; nothing was written."""


@dataclass(frozen=True)
class Generation:
    """One generated list and the record of how it was built."""

    names: list[str]
    record: dict


def fleet_policy(config: dict) -> dict:
    """The fleet-wide policy snapshot of a parsed policy file: everything but the per-machine tables."""
    return {key: value for key, value in config.items() if key not in PER_MACHINE_KEYS}


def canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def policy_digest(policy: dict) -> str:
    """SHA-256 of the canonical JSON of ``policy``, so any change to any key changes it."""
    return "sha256:" + hashlib.sha256(canonical_json(policy).encode("ascii")).hexdigest()


def limits(policy: dict) -> tuple[int, int]:
    table = policy.get("limits", {})
    return int(table["min_name_length"]), int(table["max_name_length"])


def digest_of(data: bytes, pinned: str) -> str:
    """``data``'s digest in the same form as ``pinned`` (``sha256:<hex>`` or SRI ``sha512-<b64>``)."""
    if pinned.startswith("sha256:"):
        return "sha256:" + hashlib.sha256(data).hexdigest()
    if pinned.startswith("sha512-"):
        return "sha512-" + base64.b64encode(hashlib.sha512(data).digest()).decode("ascii")
    raise NameListError(f"unsupported digest {pinned!r}: expected a sha256: or sha512- value")


def verify(data: bytes, pinned: str, url: str) -> None:
    actual = digest_of(data, pinned)
    if actual != pinned:
        raise NameListError(f"digest mismatch for {url}: pinned {pinned}, downloaded {actual}; nothing was written")


def read_member(archive: bytes, member: str, url: str) -> bytes:
    """The bytes of ``member`` inside the gzip tarball ``archive``."""
    try:
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
            handle = tar.extractfile(member)
            if handle is None:
                raise KeyError(member)
            return handle.read()
    except KeyError:
        raise NameListError(f"{url} has no member {member!r}; nothing was written") from None
    except (tarfile.TarError, OSError) as exc:
        raise NameListError(f"{url} is not a readable gzip tarball: {exc}; nothing was written") from exc


def parse_names(text: str) -> set[str]:
    """First names from one census-format file: the first field of each line, lowercased, letters only."""
    names: set[str] = set()
    for line in text.splitlines():
        fields = line.split()
        if fields:
            candidate = fields[0].lower()
            if _NAME_RE.fullmatch(candidate):
                names.add(candidate)
    return names


def parse_common_words(text: str) -> set[str]:
    """The lowercase entries of a Hunspell ``.dic`` file, which are its common words.

    The first line is the entry count. Each entry is cut at its first ``/`` (the affix
    flags) and its first tab; a capitalised entry is a proper name and is not a word.
    Affixes are never expanded: a name is compared against base forms only.
    """
    words: set[str] = set()
    for line in text.splitlines()[1:]:
        entry = line.split("/", 1)[0].split("\t", 1)[0].strip()
        if _NAME_RE.fullmatch(entry):
            words.add(entry)
    return words


def read_non_names(path: Path | None = None) -> set[str]:
    """The shipped ``non-names.txt``: tokens in the source that are not usable names."""
    target = path or DATA_DIR / NON_NAMES_FILE
    entries: set[str] = set()
    for line in target.read_text(encoding="utf-8").splitlines():
        token = line.split("#", 1)[0].strip()
        if token:
            entries.add(token)
    return entries


def filter_names(names: Iterable[str], common_words: set[str], policy: dict, non_names: set[str]) -> list[str]:
    """Apply the static filters to ``names`` and return the survivors sorted."""
    low, high = limits(policy)
    confusable = policy.get("confusable", {})
    keep = set(confusable.get("keep_names", []))
    dropped = set(policy.get("denylist", [])) | set(confusable.get("extra_words", [])) | non_names
    return sorted(
        name
        for name in set(names)
        if low <= len(name) <= high and name not in dropped and (name not in common_words or name in keep)
    )


def download(url: str, timeout: float = DOWNLOAD_TIMEOUT_S) -> bytes:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.read()
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise NameListError(f"could not download {url}: {exc}; nothing was written") from exc


def generate(
    policy: dict,
    *,
    fetch: Callable[[str], bytes] | None = None,
    non_names: set[str] | None = None,
    policy_commit: str | None = None,
    policy_committed_at: str | None = None,
    now: datetime | None = None,
) -> Generation:
    """Download, verify and filter; return the list and its record. Writes nothing."""
    fetch = fetch or download
    source = policy["source"]
    confusable = policy["confusable"]
    source_bytes = fetch(source["url"])
    verify(source_bytes, source["digest"], source["url"])
    words_bytes = fetch(confusable["words_url"])
    verify(words_bytes, confusable["words_digest"], confusable["words_url"])

    names: set[str] = set()
    for member in source["members"]:
        names |= parse_names(read_member(source_bytes, member, source["url"]).decode("ascii"))
    words = parse_common_words(read_member(words_bytes, confusable["words_member"], confusable["words_url"]).decode())
    kept = filter_names(names, words, policy, read_non_names() if non_names is None else non_names)

    by_length: dict[str, int] = {}
    for name in kept:
        by_length[str(len(name))] = by_length.get(str(len(name)), 0) + 1
    stamp = (now or datetime.now(UTC)).strftime("%Y-%m-%dT%H:%M:%SZ")
    record = {
        "schema": SCHEMA,
        "source": {"url": source["url"], "digest": source["digest"]},
        "words": {"url": confusable["words_url"], "digest": confusable["words_digest"]},
        "policy": policy,
        "policy_digest": policy_digest(policy),
        "policy_commit": policy_commit,
        "policy_committed_at": policy_committed_at,
        "count": len(kept),
        "count_by_length": dict(sorted(by_length.items(), key=lambda item: int(item[0]))),
        "generated_at": stamp,
        "writer": f"ai-cli-utils {__version__}",
    }
    return Generation(names=kept, record=record)


def render_names(names: Iterable[str]) -> str:
    return "".join(f"{name}\n" for name in names)


def render_record(record: dict) -> str:
    return json.dumps(record, indent=2, sort_keys=True) + "\n"


def write_outputs(directory: Path, generation: Generation) -> None:
    """Write ``names.txt`` and ``generation.json`` into ``directory``, each replaced atomically."""
    directory.mkdir(parents=True, exist_ok=True)
    for filename, text in (
        (NAMES_FILE, render_names(generation.names)),
        (GENERATION_FILE, render_record(generation.record)),
    ):
        fd, temp_name = tempfile.mkstemp(prefix=f".{filename}.", dir=directory)
        staged = Path(temp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
            # mkstemp creates the file owner-only; the list is shared data, readable by all.
            staged.chmod(0o644)
            staged.replace(directory / filename)
        except BaseException:
            with contextlib.suppress(FileNotFoundError):
                staged.unlink()
            raise


def read_names(path: Path) -> list[str]:
    """The names in one ``names.txt``, in file order."""
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def one_edit_apart(first: str, second: str) -> bool:
    """True when one substitution, insertion, deletion or adjacent swap turns ``first`` into ``second``."""
    if first == second:
        return False
    a, b = (first, second) if len(first) <= len(second) else (second, first)
    if len(b) - len(a) > 1:
        return False
    if len(a) == len(b):
        diffs = [i for i, (x, y) in enumerate(zip(a, b, strict=True)) if x != y]
        if len(diffs) == 1:
            return True
        return (
            len(diffs) == 2 and diffs[1] == diffs[0] + 1 and a[diffs[0]] == b[diffs[1]] and a[diffs[1]] == b[diffs[0]]
        )
    return any(a == b[:i] + b[i + 1 :] for i in range(len(b)))


def one_edit_free_subset(names: Iterable[str]) -> list[str]:
    """A large set of ``names`` no two of which are one edit apart (greedy, fewest neighbours first).

    The exact maximum is an independent-set problem; this greedy answer is a lower bound
    on it, which is the direction a capacity check needs.
    """
    pool = sorted(set(names))
    neighbours = {name: {other for other in pool if one_edit_apart(name, other)} for name in pool}
    chosen: list[str] = []
    excluded: set[str] = set()
    for name in sorted(pool, key=lambda item: (len(neighbours[item]), item)):
        if name not in excluded:
            chosen.append(name)
            excluded.add(name)
            excluded |= neighbours[name]
    return sorted(chosen)
