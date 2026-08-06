---
title: "AI-CLI-145 Store/Export Divergence Reconciliation Proposal"
category: bug
tags: [beads, reconciliation, divergence]
status: applied
template_version: "bug-1.0.0"
---

<!-- doc:region name="summary" kind="replaceable" -->

# AI-CLI-145 Store/Export Divergence Reconciliation Proposal

> This is an application plan, not an applied reconciliation. The supplied live-store
> snapshot is read-only; a session with live Dolt-store write access must perform the changes.

## Table of Contents

- [Fresh measurement](#fresh-measurement)
- [Proposed application order](#proposed-application-order)
- [Per-record dispositions](#per-record-dispositions)
- [Historical external-reference evidence](#historical-external-reference-evidence)
- [Reproduction](#reproduction)
- [Root Cause](#root-cause)
- [Fix Log](#fix-log)
- [Appendix: Evidence](#appendix-evidence)

## Fresh measurement

Measured 2026-08-06 from `live-store-snapshot.jsonl` and the checked-out
`.beads/issues.jsonl` with `python3 scripts/check_beads_divergence.py
live-store-snapshot.jsonl .beads/issues.jsonl`:

| Export | Records |
|---|---:|
| Live-store snapshot | 158 |
| Committed export | 177 |

| Difference class | Count |
|---|---:|
| Committed-only records | 20 |
| Live-only records | 1 |
| Shared-record `external_ref` disagreements | 10 |
| Other shared whole-record disagreements | 16 |

This re-measurement matches the supplied snapshot measurement; no drift was observed in these
two files. The ten reference conflicts are one systematic event, not ten independent choices:
commit `cda2d76` records that two divergent stores double-allocated `AI-CLI-143` through
`AI-CLI-156` and `AI-CLI-160`, then retained cited assignments and renumbered the other side
from `AI-CLI-162`. The `AI-CLI-160` to `AI-CLI-176` gap is therefore expected: IDs 153--156
were also reassigned in that same operation.

## Proposed application order

1. Take a new live export immediately before writing and rerun the divergence check. If any
   set or timestamp has changed, regenerate this review rather than applying this snapshot.
2. Import the 20 committed-only records into the live store, preserving their hash IDs and full
   contents. Do not synthesize replacement records.
3. For each shared mismatch, use the proposed full-record winner below. For `AI-CLI-1d2`, whose
   timestamps tie, retain the committed record because it contains both diagnostic comments.
4. Retain live-only `AI-CLI-bg7`; re-export the reconciled live store to
   `.beads/issues.jsonl` so the export includes it. Do not union-merge JSONL.
5. Validate the re-export and a new live export with the divergence check. All four difference
   classes must be zero.

## Per-record dispositions

| Record ID | Present / differing state | Proposed winner | Reason |
|---|---|---|---|
| `AI-CLI-1db` | Committed only; ref `AI-CLI-177` | Committed, import into live | No live counterpart. |
| `AI-CLI-2ff` | Committed only; ref `AI-CLI-152` | Committed, import into live | No live counterpart. |
| `AI-CLI-425c` | Committed only; ref `AI-CLI-161` | Committed, import into live | No live counterpart. |
| `AI-CLI-8v4` | Committed only; ref `AI-CLI-173` | Committed, import into live | No live counterpart. |
| `AI-CLI-9t6k` | Committed only; ref `AI-CLI-182` | Committed, import into live | No live counterpart. |
| `AI-CLI-aq0` | Committed only; ref `AI-CLI-168` | Committed, import into live | No live counterpart. |
| `AI-CLI-b01` | Committed only; ref `AI-CLI-178` | Committed, import into live | No live counterpart. |
| `AI-CLI-fcl` | Committed only; ref `AI-CLI-172` | Committed, import into live | No live counterpart. |
| `AI-CLI-i0k` | Committed only; ref `AI-CLI-150` | Committed, import into live | No live counterpart. |
| `AI-CLI-n20` | Committed only; ref `AI-CLI-146` | Committed, import into live | No live counterpart. |
| `AI-CLI-oea` | Committed only; ref `AI-CLI-151` | Committed, import into live | No live counterpart. |
| `AI-CLI-ojfd` | Committed only; ref `AI-CLI-180` | Committed, import into live | No live counterpart. |
| `AI-CLI-pn4m` | Committed only; ref `AI-CLI-179` | Committed, import into live | No live counterpart. |
| `AI-CLI-sf5c` | Committed only; ref `AI-CLI-181` | Committed, import into live | No live counterpart. |
| `AI-CLI-udn` | Committed only; ref `AI-CLI-144` | Committed, import into live | No live counterpart. |
| `AI-CLI-urv` | Committed only; ref `AI-CLI-143` | Committed, import into live | No live counterpart. |
| `AI-CLI-v0u` | Committed only; ref `AI-CLI-145` | Committed, import into live | No live counterpart. |
| `AI-CLI-v1t` | Committed only; ref `AI-CLI-148` | Committed, import into live | No live counterpart. |
| `AI-CLI-vs8` | Committed only; ref `AI-CLI-147` | Committed, import into live | No live counterpart. |
| `AI-CLI-w4p` | Committed only; ref `AI-CLI-160` | Committed, import into live | No live counterpart. |
| `AI-CLI-bg7` | Live only; ref `AI-CLI-161` | Live, retain and re-export | No committed counterpart. |
| `AI-CLI-1d2` | Shared; comments differ; both updated `2026-07-28T02:46:53Z` | Committed record | Timestamp tie; committed version retains two diagnostic comments while live has none. |
| `AI-CLI-2tv` | Shared; committed updated `2026-08-05T23:12:54Z`, live `2026-07-28T01:06:49Z` | Committed record | Later `updated_at`. |
| `AI-CLI-4ss` | Shared; committed updated `2026-08-05T23:15:43Z`, live `2026-07-29T13:08:09Z` | Committed record | Later `updated_at`. |
| `AI-CLI-50l` | Shared; ref `151` live / `170` committed; committed updated `2026-08-06T02:14:29Z` | Committed record | Later `updated_at`; historical assignment supports `170`. |
| `AI-CLI-6tq` | Shared; ref `143` live / `162` committed; committed updated `2026-08-06T02:13:44Z` | Committed record | Later `updated_at`; historical assignment supports `162`. |
| `AI-CLI-7id` | Shared; ref `144` live / `163` committed; committed updated `2026-08-06T02:13:53Z` | Committed record | Later `updated_at`; historical assignment supports `163`. |
| `AI-CLI-bc3` | Shared; ref `152` live / `171` committed; committed updated `2026-08-06T02:14:34Z` | Committed record | Later `updated_at`; historical assignment supports `171`. |
| `AI-CLI-d2p` | Shared; committed updated `2026-08-06T02:20:35Z`, live `2026-08-02T20:29:50Z` | Committed record | Later `updated_at`. |
| `AI-CLI-fae` | Shared; ref `160` live / `176` committed; committed updated `2026-08-06T02:15:04Z` | Committed record | Later `updated_at`; historical assignment supports `176`. |
| `AI-CLI-imn` | Shared; ref `148` live / `167` committed; committed updated `2026-08-06T02:14:15Z` | Committed record | Later `updated_at`; historical assignment supports `167`. |
| `AI-CLI-k1u` | Shared; committed updated `2026-08-06T02:20:38Z`, live `2026-08-02T20:29:57Z` | Committed record | Later `updated_at`. |
| `AI-CLI-lfj` | Shared; committed updated `2026-08-05T23:12:43Z`, live `2026-07-26T13:06:39Z` | Committed record | Later `updated_at`. |
| `AI-CLI-lk8` | Shared; ref `150` live / `169` committed; committed updated `2026-08-06T02:14:25Z` | Committed record | Later `updated_at`; historical assignment supports `169`. |
| `AI-CLI-u9x` | Shared; ref `145` live / `164` committed; committed updated `2026-08-06T02:13:59Z` | Committed record | Later `updated_at`; no citation assigns `145` to this record. |
| `AI-CLI-xnn` | Shared; ref `146` live / `165` committed; committed updated `2026-08-06T02:14:04Z` | Committed record | Later `updated_at`; historical assignment supports `165`. |
| `AI-CLI-z90` | Shared; ref `147` live / `166` committed; committed updated `2026-08-06T02:14:10Z` | Committed record | Later `updated_at`; historical assignment supports `166`. |

## Historical external-reference evidence

| Record ID | Historical values | Proposed value | Fleet evidence |
|---|---|---|---|
| `AI-CLI-50l` | live `151`, committed `170` | `170` | Commit `200e560` filed `151` for the other record; `cda2d76` records this reassignment. |
| `AI-CLI-6tq` | live `143`, committed `162` | `162` | [`public-repo-private-names.md#L23-L25`](public-repo-private-names.md#L23-L25) assigns `143` elsewhere; `cda2d76` preserves that citation. |
| `AI-CLI-7id` | live `144`, committed `163` | `163` | Commit `f41621f` filed `144` for the other record; `cda2d76` records this reassignment. |
| `AI-CLI-bc3` | live `152`, committed `171` | `171` | Commit `2de6d25` filed `152` for the other record; `cda2d76` records this reassignment. |
| `AI-CLI-fae` | live `160`, committed `176` | `176` | Commit `796604d` filed `160` for the other record; `cda2d76` records this reassignment. |
| `AI-CLI-imn` | live `148`, committed `167` | `167` | [`uv-hardlink-fallback-warning.md#L20-L22`](uv-hardlink-fallback-warning.md#L20-L22) assigns `148` elsewhere; `cda2d76` preserves that citation. |
| `AI-CLI-lk8` | live `150`, committed `169` | `169` | [`cc-task-namespace-persistence.md#L24-L26`](cc-task-namespace-persistence.md#L24-L26) assigns `150` elsewhere; `cda2d76` preserves that citation. |
| `AI-CLI-u9x` | live `145`, committed `164` | `164` | No fleet doc or commit cites `145` for this record. Commit `cda2d76` records `145` as an uncited pair; the later committed `updated_at` is the tiebreaker. |
| `AI-CLI-xnn` | live `146`, committed `165` | `165` | [`genai-tests-skipped-everywhere.md#L23-L25`](genai-tests-skipped-everywhere.md#L23-L25) assigns `146` elsewhere; `cda2d76` preserves that citation. |
| `AI-CLI-z90` | live `147`, committed `166` | `166` | [`dependabot-pillow-tornado-security-updates.md#L18-L20`](dependabot-pillow-tornado-security-updates.md#L18-L20) assigns `166` to this record. |

Commit `cda2d76` provides the cross-record and commit-history corroboration for these choices;
it explicitly says the cited assignments were retained rather than selecting display IDs
arbitrarily.

<!-- /doc:region name="summary" -->

<!-- doc:region name="reproduction" kind="replaceable" -->

## Reproduction

1. Export the live store to a file outside `.beads/`.
2. Run `python3 scripts/check_beads_divergence.py <live-export> .beads/issues.jsonl`.
3. Observe the four nonzero difference classes in the measurement above.

<!-- /doc:region name="reproduction" -->

<!-- doc:region name="root_cause" kind="replaceable" -->

## Root Cause

The live store and tracked export were independently changed while auto-export was blocked.
Those separate record sets made display-ID allocation calculate its next value from different
inputs, producing the duplicate allocations later repaired by `cda2d76`; subsequent store/export
changes were not brought back to one authoritative export.

<!-- /doc:region name="root_cause" -->

<!-- doc:region name="fix_log" kind="append_only" -->

## Fix Log

| Date | Commit | Notes |
|---|---|---|
| 2026-08-06 | — | Proposal created; no live-store or export write was performed. |
| 2026-08-06 | — | Applied by a session with live-store write access (sw-1). Ran `bd import .beads/issues.jsonl --json` directly against the live store: 176 rows created/matched, 12 updated (9 `external_ref` corrections matching this proposal's per-record table exactly, plus 3 status/notes corrections), 1 stale-skipped (`AI-CLI-u9x` — this proposal's own tracking issue, skipped because the applying session had edited its description moments earlier, making live's `updated_at` newer than the committed snapshot this proposal was built from). Fixed `AI-CLI-u9x`'s `external_ref` manually (`bd update AI-CLI-u9x --external-ref AI-CLI-164`, per this proposal's own disposition for it) since the bulk import correctly declined to overwrite a genuinely newer local edit. Re-exported from the reconciled live store to `.beads/issues.jsonl` (178 issues) and re-ran `check_beads_divergence.py`: **0 committed-only / 0 live-only / 0 external_ref disagreements / 0 whole-record disagreements — exports agree.** One known minor residual not pursued: `AI-CLI-1d2`'s two diagnostic comments (present in the committed version) did not merge into the tie-kept-local record; low-stakes (comment content, not a structural/display-id issue), left as a follow-up rather than blocking this fix. |

<!-- /doc:region name="fix_log" -->

<!-- doc:region name="appendix_evidence" kind="immutable" -->

## Appendix: Evidence

The fresh measurement command and the two supplied export files are the frozen evidence for
this proposal.

<!-- /doc:region name="appendix_evidence" -->
