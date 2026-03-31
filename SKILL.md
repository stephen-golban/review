---
name: review
description: |
  Code review with project-aware initialization. Auto-detects tech stack,
  generates project profile and version-specific reference docs on first run.
  Reviews staged changes, PRs, branches, commits, files, or directories.
  Runs linters, analyzes cross-file impact, detects test gaps, scans
  dependencies for vulnerabilities, learns from feedback.
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
| `init` | init | — |
| `init --update` | init-update | — |
| *(no args)* | auto | — |
| `staged` | staged | — |
| `unstaged` | unstaged | — |
| `branch` / `branch <name>` | branch | HEAD or `<name>` |
| `<PR#>` or `<PR URL>` | pr | `<PR#>` or `<URL>` |
| `<commit SHA>` | commit | `<SHA>` |
| `<SHA>..<SHA>` | commit | `<range>` |
| `<file path>` | file | `<path>` |
| `<directory>` | dir | `<path>` |
| `feedback` | feedback | show learned patterns from `feedback.jsonl` |
| `history` | history | show recent reviews from `review-history.jsonl` |

---

## MANDATORY: Init Check (runs FIRST, every time)

Before doing ANYTHING else — before parsing arguments, before any review — check if `project-profile.md` exists in this skill's directory.

**If it does NOT exist**, STOP and tell the user:

> This project hasn't been initialized yet. Run `/review init` first to set up your project profile and tech-specific references. This only takes a minute and makes every future review significantly better.

Do NOT proceed with any review mode. Do NOT offer to skip init. The only allowed actions without a profile are `init` and `init --update`.

**If it exists**, proceed normally.

---

## Tone Rules

- No compliments, filler phrases, or positive padding
- Direct, specific, concise — senior engineer talking to a peer
- Every sentence delivers information or asks a question — nothing else

---

# Init Flow

If mode is `init` or `init-update`, read and follow `init-flow.md` in this skill's directory.

---

# Review Flow

## Step 1: Prepare

Run the prep script to collect all deterministic context in one call:

```bash
python3 <skill-dir>/scripts/prep.py \
  --mode <mode> --target "<target>" --project-dir <project-root>
```

The script outputs: change summary, file clusters, risk factors, test gaps, suggested labels, profile context, diff command, reference files to read, learned patterns from feedback, previous review state.

Read the full output. If "No changes found", inform user and stop.

## Step 2: Get Diff and Source

1. Run the **diff command** from prep output
2. Read each **changed source file** in full (skip binary, lockfiles, generated code)
3. **MUST read every reference file** from the "Read These References" section of prep output. Resolve paths as `<skill-dir>/reference/<filename>`. Read each file in full using the Read tool.
   - **STOP**: Do NOT proceed to Step 3 until every listed reference file has been read. If a file is missing, note it and continue with the rest.
4. For file/dir mode: read target files directly

## Step 3: Run Analysis Tools

Each subsection below has an explicit trigger condition based on prep output. **If the trigger condition is met, you MUST run that tool.** Use the **Agent tool** to parallelize when multiple apply.

### Linters (when prep output contains "## Linters")

Run the detected linter against changed files. Parse the output and include findings.

| Linter | Command | Output |
|---|---|---|
| eslint | `npx eslint --format json <files>` | JSON array |
| biome | `npx biome check --reporter=json <files>` | JSON |
| ruff | `ruff check --output-format json <files>` | JSON array |
| prettier | `prettier --check <files>` | List of unformatted files |
| clippy | `cargo clippy --message-format=json 2>&1` | JSON lines (filter to changed files) |
| golangci-lint | `golangci-lint run --out-format json <files>` | JSON |
| rubocop | `rubocop --format json <files>` | JSON |

If the tool isn't installed or fails, skip it silently.

### Cross-File Impact (when prep output contains "## Cross-File Impact")

Use Grep to find files that import the changed modules:

```
For each changed source file, grep the project for import/require/use statements referencing it.
Report files that import changed modules but aren't in the changeset.
```

Skip `node_modules`, `.git`, `dist`, `build`, `target`, `vendor`, `__pycache__`.

### Dependency Vulnerabilities (when prep output contains "## Dependency Changes Detected")

Run the appropriate audit tool:

| Dep File | Command |
|---|---|
| package.json/lock | `npm audit --json` |
| Cargo.toml/lock | `cargo audit --json` |
| requirements.txt | `pip-audit --format json` |
| go.mod | `govulncheck -json ./...` |

If no audit tool available, skip with a note.

## Step 4: Analyse

### 4a. Change Walkthrough

Using file clusters and changed functions from prep output:
1. Write a 2-3 sentence summary of what changed and why
2. Group related files and describe each group's purpose in one line
3. If branch/PR mode and prep output contains "## Commit History": read the commit progression to understand the developer's intent and iteration order. Use this to:
   - Distinguish intentional design choices from oversights (e.g., a file refactored in commit 2 after being added in commit 1 — the final state is intentional)
   - Avoid flagging patterns that were deliberately introduced (commit message says "switch to X approach")
   - Identify when a later commit may have broken something that worked in an earlier commit

### 4b. Review Protocol

**For each changed file**, apply the following checks **in order**. Do not skip items — if a check has no findings for a file, move to the next check.

1. **Blocking patterns** — compare each change against every pattern in the "Blocking Patterns (from profile)" section of prep output. Any match is `[blocking]`.
2. **Priority focus areas** — apply extra scrutiny to changes touching areas listed in "Priority Focus (from profile)" section of prep output.
3. **Generated reference files** — for each finding you consider raising, check whether the reference files you read in Step 2 contain a relevant rule. Cite the reference if so.
4. **Linter findings** (if Step 3 produced them) — deduplicate against your own findings, promote genuine issues.
5. **Cross-file impact** (if Step 3 produced results) — flag dependents that import changed modules but are not in the changeset.
6. **Test coverage gaps** from prep "Test Coverage Gaps" section — suggest specific test cases.
7. **Dependency vulnerabilities** (if Step 3 produced results) — `[blocking]` if critical/high severity.
8. **Learned patterns** from prep "Learned Patterns" section — suppress previously dismissed findings, apply corrections the user has taught.
9. **Your own knowledge** of detected languages, frameworks, and their detected versions.

## Step 5: Validate Findings

**STOP: Do NOT present findings to the user, post to a PR, or proceed to Step 6 until validation is complete for every `[blocking]` and `[important]` finding.**

For each `[blocking]` or `[important]` finding:

1. If the finding came from a reference file → use the source link already in that file. Confirmed.
2. If no source link exists → search via `mcp__Ref__ref_search_documentation` or `mcp__Ref__ref_read_url` to verify the claim is correct for the detected version.
3. **Confirmed** → keep. Attach source link + fix snippet.
4. **Wrong/outdated** → drop silently. Do NOT include it in the report.
5. **Inconclusive** (cannot confirm or deny after searching) → keep with `(unverified)` marker.

**Validation is NOT required for**: missing await, null deref, unused vars, hardcoded secrets, linter-reported findings. These are self-evident from the code.

After validation, re-check: does the finding still apply given the full context of the change (commit history, surrounding code, other files in the changeset)? If not, drop it.

## Step 6: Report

Check report mode from prep output. **If mode is humanized, skip Standard Mode entirely — go directly to Humanized Mode below.** Do NOT read the Standard Mode template as a starting point for humanized output.

### Standard Mode

```
## Change Walkthrough
<2-3 sentence summary>

### File Groups
- **auth/** — Updated login flow (3 files)

## Issues Found

### [blocking]
1. **file.ts:42** — Description. Why it matters.
   ```ts
   // Fix
   <corrected code>
   ```
   Source: <URL>

### [important] / [nit] / [suggestion]
...

## Test Suggestions
- <specific test cases for uncovered changes>
```

Omit empty sections. Zero issues → "No issues found."

For complex changes (complexity > 0.5 or files > 5), include a Mermaid diagram:
```mermaid
graph LR
  A[file.ts] --> B[handler.ts] --> C[db.ts]
```

### Humanized Mode

Humanized mode produces prose paragraphs that read like a senior developer's Slack message. It is NOT standard mode with labels removed.

**NEVER use in humanized mode**:
- Section headers (`##`, `###`, or any markdown heading)
- Bullet lists or numbered lists
- Severity labels (`[blocking]`, `[important]`, `[nit]`)
- Code block fences for fix suggestions (use inline backticks only)
- Summary tables or structured formatting of any kind
- AI tells: "heads up", "nit:", "it's worth noting", "consider", "ensure", "I noticed that", "LGTM", "I'd suggest"

**ALWAYS in humanized mode**:
- Write in lowercase, casual prose. Short sentences. Paragraphs, not lists.
- Refer to code with inline backticks: `functionName`
- Ask questions naturally: "any reason to go with X here?"
- Group related thoughts into the same paragraph

**Example — WRONG** (standard mode habits leaking in):

> ## Issues Found
> ### [blocking]
> 1. **auth.ts:42** — Missing input validation...

**Example — RIGHT**:

> the main thing that jumped out is auth.ts around line 42 — the user input goes straight into the query without validation, which is a sql injection risk. wrapping it in `sanitizeInput()` would fix it. also worth a look: the retry logic in `fetchData` doesn't have a backoff, so if the upstream is down you'll hammer it pretty fast.

Zero issues → "looks clean, nothing jumped out."

## Step 7: Apply Fixes (Optional)

After the report, offer to apply fixes:

> Apply fixes? (all, #1 #3, or skip)

Use Edit to apply directly. In humanized mode: "want me to fix these?"

**STOP: WAIT for the user's response before proceeding to Step 8.** Do NOT combine this prompt with the feedback prompt. Handle the user's fix request (or skip) completely before moving on.

## Step 8: Record

### Save review state (for incremental reviews next time)

For PR/branch modes, update `review-state.json` in the skill directory.

Entry format:
```json
{"<mode>/<target>": {"last_reviewed_sha": "<HEAD>", "last_review_date": "<ISO8601>", "findings_count": N, "previous_findings": ["summary1", "summary2"]}}
```

**MUST use Read then Edit, NOT Write.** The file may contain entries from previous reviews of other branches/PRs. Steps:
1. **Read** `review-state.json` with the Read tool. If it doesn't exist, create it with Write containing only the new entry.
2. **Edit** the file to add/update the entry for `<mode>/<target>`, preserving all other entries.
3. NEVER use Write to overwrite this file when it already exists.

### Log to history

Append one JSON line to `review-history.jsonl` in the skill directory:

```json
{"timestamp": "<ISO8601>", "mode": "<mode>", "target": "<target>", "files_changed": N, "findings": {"blocking": N, "important": N, "nit": N, "suggestion": N}}
```

### Collect feedback

After delivery (and after any fix application from Step 7 is complete), ask:

> Dismiss any findings? (# to dismiss, or skip)

**STOP: WAIT for the user's response.** Do NOT combine this prompt with the delivery/posting prompt. Process dismissals before ending.

For each dismissed finding, append to `feedback.jsonl` in the skill directory:

```json
{"id": "<uuid>", "timestamp": "<ISO8601>", "type": "dismiss", "finding": "<description>", "file_pattern": "<*.ext>", "reason": "<user explanation>"}
```

### Deliver

**All non-PR modes** — present report directly in conversation. Skip the rest of this section.

**PR mode** — preview, confirm, then post.

If profile has `Offer PR posting: yes`:

**1. Resolve format**: Check the profile's `Posting format` field. If it specifies `single` or `inline`, use that — do NOT ask the user again. Only ask if the field is missing or empty:

> Post to PR? Choose format:
> 1. **Single comment** — full review as one summary comment
> 2. **Inline comments** — each finding as a comment on the relevant line
> 3. **Skip** — don't post, just show here

**2. Redact secrets**: Before previewing or posting, scan all comment text for patterns matching secrets (API keys, tokens, passwords, connection strings, private keys, `.env` values). Replace any detected secret with `[REDACTED]`. Never reproduce raw credentials in PR comments.

**3. Preview**: Show the **exact text** that will be posted. Not a summary table, not a description of the content — the actual comment body (or for inline mode, show each comment with its file:line target). The user must see what will appear on GitHub. **The posted text MUST match the report mode** — if humanized, the PR comments are humanized prose, not structured markdown.

**4. Confirm**: Ask: "post this? (yes / skip)"

**STOP: WAIT for user confirmation before posting anything.**

**5. Post** based on resolved format:
- **Single comment** → `gh pr review <PR#> --comment --body "..."`
- **Inline comments** → post each finding individually: `gh api repos/{owner}/{repo}/pulls/{pr}/comments --method POST -f body="<finding>" -f path="<file>" -f commit_id="$(gh pr view <PR#> --json headRefOid -q .headRefOid)" -F line=<line>`. Post one comment per finding. Do NOT batch into a single API call.
- **Skip** → do nothing

**6. Apply labels** (only if posting was done, not if skipped): `gh pr edit <PR#> --add-label "<labels>"` using suggested labels from prep output.
