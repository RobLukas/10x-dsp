---
name: write-adr
description: Use when a change adds or swaps a major dependency, changes an architectural pattern, introduces a new integration pattern, or changes a database or snapshot schema.
---

# Write an ADR

Create `/docs/adr/NNNN-short-name.md` (next free number) for:

1. Major dependency changes
2. Architectural pattern changes
3. New integration patterns
4. Database or snapshot schema changes

Also write one when a change alters what a measurement means (e.g. hot path, environment, resource settings).

## Template

```markdown
# NNNN. Title

- Status: proposed | accepted | superseded by NNNN
- Date: YYYY-MM-DD

## Context
What forces are at play? Which success criterion or experiment does this serve?

## Decision
What we will do, in one or two paragraphs.

## Alternatives considered
Each with the reason it was not chosen.

## Consequences
Positive, negative, and the effect on measurements (baseline, run comparability).
```

## Rules

- Keep it to one page.
- Link the ADR from the relevant `/docs` page and add a `CHANGELOG.md` entry.
- Never rewrite an accepted ADR; supersede it with a new one.