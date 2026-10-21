---
name: git-workflow
description: Use when creating commits, branches, including conventional commit format, scopes.
allowed-tools: Bash(git add *) Bash(git commit *) Bash(git status *) Bash(git checkout *) Bash(git pull *)
---

# Git workflow

## Branches

`type/short-description`, e.g. `feat/inprocess-scorer`, `fix/redis-pipeline-timeout`, `exp/json-vs-protobuf`. Use `exp/` for experiment variants.

## Commits (Conventional Commits)

Format: `type(scope): description`

- Types: `feat`, `fix`, `docs`, `style`, `refactor`, `test`, `chore`, `perf`.
- Scopes: `dsp`, `core`, `ssp`, `prediction`, `inventory`, `dashboard`, `scenario`, `infra`, `docs`.
- Mark breaking changes with `!` or a `BREAKING CHANGE:` footer. Changing the hot path or the result file format counts as breaking.
- Explain why, not just what. One logical change per commit.
- Reference issues in the footer where they exist.
