---
name: review
description: |
  Fast, deep code review that finds real bugs. Auto-detects tech stack
  on first run. Reviews PRs, branches, commits, staged changes, files,
  or directories. Runs static analysis, linters, cross-file impact.
  Learns from feedback. Zero external dependencies required.
  Use when the user asks to review code, check a PR, audit a file,
  or runs /review, /review init, /review staged, /review <PR#>,
  /review branch, /review <commit>, /review <file/dir>.
allowed-tools:
  - Read
  - Write
  - Edit
  - Grep
  - Glob
  - Bash
  - Agent
  - AskUserQuestion
  - mcp__Ref__ref_search_documentation
  - mcp__Ref__ref_read_url
---

# Code Review

## Argument Router

| Input | Mode | Target |
|---|---|---|
| `init` | init | - |
| `init --update` | init-update | - |
| *(no args)* | auto | - |
| `staged` | staged | - |
| `unstaged` | unstaged | - |
| `branch` / `branch <name>` | branch | HEAD or `<name>` |
| `<PR#>` or `<PR URL>` | pr | `<PR#>` or `<URL>` |
| `<commit SHA>` | commit | `<SHA>` |
| `<SHA>..<SHA>` | commit | `<range>` |
| `<file path>` | file | `<path>` |
| `<directory>` | dir | `<path>` |
| `feedback` | feedback | show learned patterns from `feedback.jsonl` |
| `history` | history | show recent reviews from `review-history.jsonl` |

---

## Init Check (runs FIRST, every time)

Check if `project-profile.md` exists in this skill's directory.

**If missing**, STOP: "Run `/review init` first." Do NOT proceed. Only `init` and `init --update` allowed without a profile.

---

## Tone Rules

- No compliments, filler, or positive padding
- Direct, specific, concise - senior engineer to a peer
- Every sentence delivers info or asks a question
- Never use em dash (U+2014). Hyphen-minus only.
- Short sentences. One comma-clause max.
- No narration. No "let me read...", "now I'll analyze...". User sees prep confirmation, then the review.

---

## Mode Dispatch

**init / init --update**: Read and follow `init-flow.md` in this skill's directory.

**feedback**: Read `feedback.jsonl`, format learned patterns with category stats, show to user.

**history**: Read `review-history.jsonl`, format recent reviews, show to user.

**All other modes** (review): Read and follow `review-flow.md` in this skill's directory.
