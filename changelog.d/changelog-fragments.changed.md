- A changelog entry now goes in its own file, `changelog.d/<name>.<type>.md`, instead of at the
  top of `CHANGELOG.md`, so two open pull requests no longer conflict on the changelog.
  `scripts/fold_changelog.py` folds the fragments into `[Unreleased]` at release time, in the same
  layout as entries written by hand, and the commit-msg hook accepts a fragment for a `feat:`
  commit. See CONTRIBUTING.md, Changelog Fragments.
