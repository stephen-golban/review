# /review

AI code review skill for Claude Code. Project-aware, learns from feedback, runs your linters, traces cross-file impact, detects test gaps, scans dependencies. Your code never leaves your machine.

## Install

```bash
npx skills add stephen-golban/review
```

## Setup

```
/review init
```

Scans your project, asks a few questions, researches your actual tech stack and versions, then generates:
- `project-profile.md` — your review preferences, conventions, blocking patterns
- `reference/<tech>.md` — concise review briefs with source links (50-120 lines each)

Run once per project. Update anytime with `/review init --update`.

## Usage

```
/review                     # auto-detect: staged → unstaged → branch diff
/review staged              # staged changes only
/review unstaged            # working directory changes
/review 42                  # PR #42
/review branch              # current branch vs base
/review a1b2c3d             # single commit
/review src/auth/login.ts   # audit a file
/review src/auth/           # audit a directory
/review feedback            # view learned patterns
/review history             # recent reviews
```

## What It Does

- **Change walkthrough** — summarizes what changed and why, groups related files
- **Linter integration** — runs your eslint/biome/ruff/clippy/etc on changed files
- **Cross-file impact** — greps imports to find affected files not in the changeset
- **Test gap analysis** — identifies source files missing test updates
- **Dependency scanning** — runs `npm audit`/`cargo audit`/etc on changed deps
- **Feedback learning** — remembers corrections, won't repeat dismissed findings
- **Incremental reviews** — reviews only new commits when revisiting a PR
- **Auto-labeling** — classifies changes as feature/bugfix/refactor/security/etc
- **One-click fixes** — applies fixes directly after the review
- **Humanized mode** — output reads like a human developer, no AI tells

## Report Modes

- **Standard** — severity labels, fix snippets, source links, diagrams
- **Humanized** — reads like a human wrote it, casual tone, no AI tells

## How It Works

```
/review staged
    │
    ├─ prep.py              ← deterministic: file analysis, risk factors,
    │                          test gaps, labels, clusters, feedback (1 call)
    │
    ├─ read diff + sources  ← only the changed files + matched references
    │
    ├─ run linters          ← LLM runs eslint/ruff/etc natively (if available)
    ├─ grep for imports     ← LLM traces cross-file impact (if multi-file)
    ├─ run audit tools      ← LLM runs npm audit/etc (if deps changed)
    │
    ├─ analyse              ← walkthrough + review against profile, refs,
    │                          linter output, impact, test gaps, feedback
    │
    ├─ validate             ← blocking/important verified against docs
    ├─ report               ← standard or humanized format
    ├─ apply fixes          ← optional: edit files directly
    └─ record               ← save state + history + collect feedback
```

## File Structure

```
SKILL.md              ← skill entry point + review flow
init-flow.md          ← init instructions (loaded only when needed)
scripts/
  scanner.py          ← project detection for init (Python 3.8+ stdlib)
  prep.py             ← deterministic review prep (Python 3.8+ stdlib)
reference/
  .gitkeep
```

Two scripts. Everything else is the LLM using its native tools.

## Design Principles

- **The LLM is the brain.** Scripts handle only deterministic grunt work (file classification, risk scoring, diff parsing). Analysis, validation, and reporting are LLM-directed.
- **No shipped references.** Generated refs contain only version-specific, source-linked findings from real research.
- **Init runs once, reviews run lean.** ~4K tokens of framework overhead per review.
- **Graceful degradation.** Missing linter? Skipped. No `gh`? Skip PR features.
- **Python 3.8+ stdlib only.** No pip install required.
