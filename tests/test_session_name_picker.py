"""The human-name picker (T-3.2).

The property tests compare every pick against an oracle written from the design's
wording alone: its own edit distance, its own tier ladder, its own run. A random
generator with a fixed seed per case builds the name lists and the held sets, so a
failure names a seed that reproduces it.
"""

from __future__ import annotations

import random
import string

import pytest

from ai_cli import session_naming as sn

STEM = "myrepo-box"
ALIAS = "myrepo"


def _request(generated, *, held=(), held_titles=(), floor=3, letters=None, **overrides) -> sn.PickRequest:
    fields = {
        "generated": frozenset(generated),
        "held": frozenset(held),
        "held_titles": frozenset(held_titles),
        "stem": STEM,
        "title_prefix": f"{STEM}-",
        "repo_alias": ALIAS,
        "min_length": 3,
        "max_length": 5,
        "letters": frozenset(letters if letters is not None else sn.alias_letters(ALIAS)),
        "pick_floor": floor,
    }
    fields.update(overrides)
    return sn.PickRequest(**fields)


def _distance(a: str, b: str) -> int:
    """Optimal-string-alignment distance: substitution, insertion, deletion, adjacent swap."""
    rows = [[0] * (len(b) + 1) for _ in range(len(a) + 1)]
    for i in range(len(a) + 1):
        rows[i][0] = i
    for j in range(len(b) + 1):
        rows[0][j] = j
    for i in range(1, len(a) + 1):
        for j in range(1, len(b) + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            rows[i][j] = min(rows[i - 1][j] + 1, rows[i][j - 1] + 1, rows[i - 1][j - 1] + cost)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                rows[i][j] = min(rows[i][j], rows[i - 2][j - 2] + 1)
    return rows[len(a)][len(b)]


def _oracle_run(request: sn.PickRequest) -> tuple[set[str], set[str]]:
    """(the run the design says a pick must come from, every free candidate)."""
    free = set()
    for name in request.generated:
        title = f"{request.title_prefix}{name}"
        prefix_pair = any(
            held.startswith(f"{request.stem}-") and held != title and (held.startswith(title) or title.startswith(held))
            for held in request.held_titles
        )
        if (
            request.min_length <= len(name) <= request.max_length
            and name not in request.held
            and name not in request.denylist
            and name not in request.reserved_words
            and not any(_distance(name, held) == 1 for held in request.held)
            and not prefix_pair
        ):
            free.add(name)
    if not free:
        return set(), free
    shortest = min(map(len, free))
    pool = [name for name in free if len(name) == shortest]
    first = ALIAS[0]
    tiers = sorted({(len(set(n) & request.letters), n.startswith(first)) for n in pool}, reverse=True)
    run: set[str] = set()
    for tier in tiers:
        run |= {n for n in pool if (len(set(n) & request.letters), n.startswith(first)) == tier}
        if len(run) >= request.pick_floor:
            break
    return run, free


def _random_case(seed: int) -> sn.PickRequest:
    rng = random.Random(seed)
    alphabet = "myreoabcdi"
    generated = {"".join(rng.choice(alphabet) for _ in range(rng.randint(2, 6))) for _ in range(rng.randint(1, 60))}
    held = set(rng.sample(sorted(generated), k=rng.randint(0, min(8, len(generated)))))
    held |= {"".join(rng.choice(alphabet) for _ in range(rng.randint(3, 5))) for _ in range(rng.randint(0, 3))}
    held_titles = {f"{STEM}-{name}" for name in held}
    return _request(generated, held=held, held_titles=held_titles, floor=rng.randint(1, 12))


# Seeds run inside one test rather than as parameters: this suite's per-test fixtures cost
# far more than a pick, and the assertion message names the seed that fails.
SEEDS = range(400)


def test_given_random_lists_and_held_sets_when_picked_then_no_shorter_free_candidate_existed():
    exhausted = 0
    for seed in SEEDS:
        request = _random_case(seed)
        _run, free = _oracle_run(request)
        if not free:
            exhausted += 1
            with pytest.raises(sn.NamePoolExhaustedError):
                sn.pick_name(request, random.Random(seed))
            continue
        pick = sn.pick_name(request, random.Random(seed))

        assert pick.name in free, seed
        assert not [name for name in free if len(name) < len(pick.name)], seed
    assert exhausted < len(SEEDS) // 4, "the generator rarely produces a pickable case"


def test_given_random_lists_when_picked_then_the_pick_comes_from_the_smallest_top_run_reaching_the_floor():
    for seed in SEEDS:
        request = _random_case(seed)
        run, free = _oracle_run(request)
        if not free:
            continue

        pick = sn.pick_name(request, random.Random(seed))

        assert pick.name in run, seed
        assert pick.run_size == len(run), seed
        assert all(len(name) == len(pick.name) for name in run), f"seed {seed}: the floor admitted a longer name"


def test_given_a_floor_no_run_reaches_when_picked_then_every_candidate_of_the_shortest_length_is_in_the_run():
    request = _request({"ada", "bob", "cyd", "myrena"}, floor=50)

    seen = {sn.pick_name(request, random.Random(seed)).name for seed in range(200)}

    assert seen == {"ada", "bob", "cyd"}


def test_given_a_strong_four_letter_tier_and_a_weak_three_letter_name_when_picked_then_the_short_name_wins():
    request = _request({"bob", "mery", "myra", "remy"}, floor=1)

    assert sn.pick_name(request, random.Random(0)).name == "bob"


def test_given_a_top_tier_at_the_floor_when_picked_then_lower_tiers_are_never_drawn():
    # m, y, r, e, p, o: "myr" holds three letters and starts with m; "rye" three without m.
    request = _request({"myr", "rye", "bob", "abc"}, floor=1)

    assert {sn.pick_name(request, random.Random(seed)).name for seed in range(100)} == {"myr"}


def test_given_an_include_set_when_picked_then_it_is_the_letter_set_used():
    # "o" alone: "bob" contains it and "myr", which holds three alias letters, does not.
    letters, warning = sn.resolve_letters(ALIAS, ["o"], "config/session-names.toml")
    request = _request({"myr", "bob"}, floor=1, letters=letters)

    assert warning is None
    assert letters == frozenset({"o"})
    assert sn.pick_name(request, random.Random(0)).name == "bob"


def test_given_a_configured_letter_outside_the_alias_when_resolved_then_the_alias_letters_are_used_with_one_warning():
    letters, warning = sn.resolve_letters(ALIAS, ["m", "z"], "/fleet/config/session-names.toml")

    assert letters == sn.alias_letters(ALIAS)
    assert warning is not None
    assert "/fleet/config/session-names.toml" in warning
    assert "z" in warning


@pytest.mark.parametrize(
    ("candidate", "held", "why"),
    [
        ("ada", {"ada"}, "held"),
        ("ada", {"ida"}, "substitution"),
        ("ann", {"anna"}, "insertion"),
        ("anna", {"ann"}, "deletion"),
        ("abe", {"bae"}, "adjacent swap"),
    ],
)
def test_given_a_name_one_edit_from_a_held_name_when_picked_then_it_is_never_returned(candidate, held, why):
    request = _request({candidate, "zzzz"}, held=held, floor=1)

    picks = {sn.pick_name(request, random.Random(seed)).name for seed in range(50)}

    assert picks == {"zzzz"}, why


@pytest.mark.parametrize(
    ("overrides", "excluded"),
    [
        ({"denylist": frozenset({"ada"})}, "ada"),
        ({"reserved_words": frozenset({"ada"})}, "ada"),
        ({"min_length": 4}, "ada"),
        ({"max_length": 2}, "ada"),
    ],
)
def test_given_a_name_a_rule_forbids_when_picked_then_it_is_never_returned(overrides, excluded):
    generated = {"ada", "otto"} if overrides.get("max_length") != 2 else {"ada", "ot"}
    request = _request(generated, floor=1, **{"min_length": 2, **overrides})

    picks = {sn.pick_name(request, random.Random(seed)).name for seed in range(50)}

    assert excluded not in picks


def test_given_a_hand_added_name_failing_the_static_filters_when_picked_then_it_is_never_returned():
    request = _request({"otto"}, floor=1, manual=frozenset({"ada", "ida"}), passes_static=lambda name: name == "ida")

    picks = {sn.pick_name(request, random.Random(seed)).name for seed in range(100)}

    assert "ada" not in picks
    assert "ida" in picks


def test_given_no_static_check_available_when_picked_then_no_hand_added_name_is_returned():
    request = _request({"otto"}, floor=1, manual=frozenset({"ida"}))

    assert sn.pick_name(request, random.Random(0)).name == "otto"


@pytest.mark.parametrize(
    "held_title",
    [f"{STEM}-evan", f"{STEM}-ev"],
    ids=["candidate-is-prefix-of-held", "held-is-prefix-of-candidate"],
)
def test_given_a_strict_prefix_title_pair_with_the_same_stem_when_picked_then_the_name_is_never_returned(held_title):
    request = _request({"eva", "zzzz"}, held_titles={held_title}, floor=1, max_edit_distance=0)

    picks = {sn.pick_name(request, random.Random(seed)).name for seed in range(50)}

    assert picks == {"zzzz"}


def test_given_a_prefix_title_under_another_stem_when_picked_then_the_name_stays_a_candidate():
    request = _request({"eva"}, held_titles={"other-box-evan"}, floor=1)

    assert sn.pick_name(request, random.Random(0)).name == "eva"


def test_given_every_name_filtered_out_when_picked_then_the_refusal_names_each_exhausted_filter():
    request = _request({"ada", "ida", "toolong", "obo"}, held={"ada"}, reserved_words=frozenset({"obo"}))

    with pytest.raises(sn.NamePoolExhaustedError) as caught:
        sn.pick_name(request, random.Random(0))

    message = str(caught.value)
    assert "held: 1" in message
    assert "within 1 edit of a held name: 1" in message
    assert "outside the length limits: 1" in message
    assert "equal to a repo or host alias: 1" in message


def test_given_fewer_free_names_than_low_water_when_warned_then_the_warning_names_the_remedies():
    pick = sn.pick_name(_request({"ada", "bob"}, floor=1), random.Random(0))

    warning = sn.low_water_warning(pick, low_water=25)

    assert warning is not None
    assert "ai list-name-candidates" in warning
    assert "max_name_length" in warning
    assert sn.low_water_warning(pick, low_water=2) is None


def test_given_a_pick_when_reported_then_the_tier_names_its_length_letters_and_first_letter():
    pick = sn.pick_name(_request({"myr"}, floor=1), random.Random(0))

    assert pick.tier == "len3/3/first"


def test_given_random_pairs_when_compared_then_one_edit_apart_matches_the_distance_oracle():
    rng = random.Random(0)
    for _ in range(10_000):
        a = "".join(rng.choice(string.ascii_lowercase[:4]) for _ in range(rng.randint(1, 5)))
        b = "".join(rng.choice(string.ascii_lowercase[:4]) for _ in range(rng.randint(1, 5)))
        assert sn.one_edit_apart(a, b) == (_distance(a, b) == 1), (a, b)
