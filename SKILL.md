---
name: review
description: |
  Fast, deep code review that outperforms automated review tools.
  Auto-detects tech stack on first run, then reviews PRs, branches,
  commits, staged changes, files, or directories. Runs linters,
  detects test gaps, scans dependencies, learns from feedback.
  Prioritizes finding real bugs over ceremony.
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

## MANDATORY: Init Check (runs FIRST, every time)

Before doing ANYTHING else - before parsing arguments, before any review - check if `project-profile.md` exists in this skill's directory.

**If it does NOT exist**, STOP and tell the user:

> This project hasn't been initialized yet. Run `/review init` first to set up your project profile and tech-specific references. This only takes a minute and makes every future review significantly better.

Do NOT proceed with any review mode. Do NOT offer to skip init. The only allowed actions without a profile are `init` and `init --update`.

**If it exists**, proceed normally.

---

## Tone Rules (apply to ALL modes)

- No compliments, filler phrases, or positive padding ("solid", "clean", "great", "nice", "looks good")
- Direct, specific, concise - senior engineer talking to a peer
- Every sentence delivers information or asks a question - nothing else
- Never output the em dash character (the long unicode dash, U+2014). Always use the regular hyphen-minus.
- Short sentences. If a sentence has more than one comma-separated clause, split it.

---

# Init Flow

If mode is `init` or `init-update`, read and follow `init-flow.md` in this skill's directory.

---

# Review Flow

## Speed Target

Deliver the review within 90 seconds. Every operation that can run in parallel MUST run in parallel. Every stop that can be eliminated MUST be eliminated. Time spent on ceremony is time not spent finding bugs.

## Step 1: Prepare

Run the prep script:

```bash
python3 <skill-dir>/scripts/prep.py \
  --mode <mode> --target "<target>" --project-dir <project-root>
```

Read the output. If "No changes found", tell user and stop.

## Step 2: Gather

After reading prep output, collect everything needed for review. **Parallelize aggressively.**

### Always (every review)

Do these directly (no Agent overhead needed):

1. Run the **diff command** from prep output
2. Read each **changed source file** in full (skip binaries, lockfiles, generated code)
3. Read **reference files** from "Read These References" section. Only read refs that match languages in the changeset - if the changeset is pure TypeScript, skip unrelated refs like Rust or Python.
4. For file/dir mode: read targets directly

### Conditional tools (launch via Agent when multiple apply)

| Tool | Trigger | What to run |
|---|---|---|
| Linter | Prep has "## Linters" | See linter table |
| Dep audit | Prep has "## Dependency Changes Detected" | See audit table |

**Parallelism rules:**
- Two+ tools triggered: launch each as a separate Agent task, proceed with direct reads while agents run
- One tool triggered: run it directly, no Agent overhead
- Zero triggered: skip entirely

### Linter commands

| Linter | Command |
|---|---|
| eslint | `npx eslint --format json <files>` |
| biome | `npx biome check --reporter=json <files>` |
| ruff | `ruff check --output-format json <files>` |
| prettier | `prettier --check <files>` |
| clippy | `cargo clippy --message-format=json 2>&1` |
| golangci-lint | `golangci-lint run --out-format json <files>` |
| rubocop | `rubocop --format json <files>` |

Skip silently if tool isn't installed.

### Dependency audit commands

| Dep File | Command |
|---|---|
| package.json/lock | `npm audit --json` |
| Cargo.toml/lock | `cargo audit --json` |
| requirements.txt | `pip-audit --format json` |
| go.mod | `govulncheck -json ./...` |

Skip if no audit tool available.

## Step 3: Deep Review

You are hunting for bugs. Not confirming the code compiles. Not rubber-stamping.

### Mindset

**Assume the code has defects. Your job is to find them.**

Do NOT talk yourself out of findings. If something looks suspicious, investigate it. Verify it against the code. If it holds up, flag it.

If you found zero issues on a non-trivial change (>50 lines, multiple files, or new logic), you probably missed something. Go back and look harder - trace execution paths, check edge cases, look at what WASN'T changed that should have been.

But NEVER invent findings to fill space. Three verified, real catches are worth more than eight observations where half are wrong. Your credibility is the product. Every bogus finding costs trust that takes ten good findings to rebuild.

### Execution tracing

For each significant changed function or code block, trace the execution path mentally:

1. **Inputs** - what calls this? What are all possible input shapes, including adversarial ones?
2. **Boundaries** - what happens with null, undefined, empty string, zero, negative, NaN, too-large, malformed, duplicate?
3. **Concurrency** - what if this is called twice simultaneously? Rapidly? While another operation is mid-flight?
4. **State** - what state changes? Can state be left inconsistent if this throws halfway through?
5. **Errors** - what can throw? Are all error paths handled? What does the caller see on failure?
6. **Resources** - are connections, handles, listeners, subscriptions, timers released on ALL paths including error and early-return?

### Bug patterns to actively hunt

Check every changed file against these. Do not skip any category.

**Logic bugs:**
- Off-by-one in loops, slice, substring, array access, pagination
- Wrong comparison (== vs ===, > vs >=, && vs ||)
- Missing break/return causing fallthrough
- Incorrect argument order in function calls
- Stale closures capturing variables by reference

**Null safety:**
- Optional chaining that assumes non-null downstream
- Nullable returns used without null checks
- Destructuring with missing properties
- Array access without bounds check

**Async and concurrency:**
- Missing await on async calls
- Race conditions from shared mutable state
- Promise.all with dependent mutations
- Unhandled promise rejections
- TOCTOU (check-then-act without atomicity)

**Resource management:**
- addEventListener without removeEventListener
- setInterval/setTimeout without cleanup
- Database connections not released on error paths
- Stream/file handles not closed in finally blocks
- Subscriptions (RxJS, event emitters) without unsubscribe

**Error handling:**
- Empty catch blocks or `.catch(() => {})`
- Catching too broadly (catch Exception vs specific types)
- Errors logged but not re-thrown or handled
- Missing error handling on external calls (API, DB, file I/O)
- Default values masking real failures

**Security:**
- SQL/NoSQL injection (string interpolation in queries)
- XSS (unescaped user input in HTML/templates)
- Path traversal (user input in file paths)
- Command injection (user input in shell commands)
- Auth bypass (missing auth checks on endpoints)
- Secrets in source code
- SSRF (user-controlled URLs in server-side requests)
- Prototype pollution (object spread/merge with user input)
- Insecure deserialization

**Performance:**
- N+1 queries (DB call inside a loop)
- Unbounded loops or recursion
- Missing pagination on list endpoints
- Blocking I/O on hot paths
- Unnecessary re-renders or recomputation
- Large objects passed by value when reference would suffice
- Missing memoization on expensive pure functions

**Incomplete changes:**
- Imports of deleted/renamed modules
- Half-migrated patterns (old + new style in same file)
- Dead code left from refactor
- Missing updates to related files (types, tests, docs, configs)
- Breaking changes to exported APIs without updating consumers

### Cross-reference with project context

Apply in this order, but do NOT treat this as a checklist to plod through. Use it as augmentation for your analysis:

1. **Blocking patterns** from profile - any match is `[blocking]`
2. **Priority focus areas** from profile - extra scrutiny
3. **Reference file rules** - cite source links when applicable
4. **Linter results** - deduplicate against your findings, promote real issues
5. **Dep vulnerabilities** - `[blocking]` if critical/high severity
6. **Learned patterns** from feedback - respect previous dismissals
7. **Cross-file impact** - use Grep to find files importing changed modules that are NOT in the changeset. Flag breaking changes to exported APIs.
8. **Test gaps** from prep output - flag missing test coverage for new logic, suggest specific test cases

### Verify (MANDATORY before any finding is presented)

You are putting your name on this review. Every finding you present must be defensible to a staff engineer who knows the codebase better than you. If you'd be embarrassed when the developer replies "that's not how this works", you haven't verified enough.

**For EVERY finding, before including it:**

1. **Re-read the actual lines** - not from memory, from the diff and source you read in Step 2. Confirm the code actually does what you think it does.
2. **Check surrounding context** - read 20-30 lines above and below. Is there a guard clause you missed? A comment explaining why? A try/catch wrapping it? A type that makes the null case impossible?
3. **Check callers/consumers** - if you're flagging an API change or missing validation, grep to see how this code is actually called. Maybe the caller already validates. Maybe there's only one caller and it always passes valid input.
4. **Verify your fix compiles** - mentally trace your suggested fix. Does it use APIs that actually exist in this codebase? Does it match the patterns used elsewhere in the project? Would it introduce a new bug?
5. **Check against reference files** - if a reference file covers this topic, use its rules and source links. If your finding contradicts a reference file rule, drop your finding.

**When to do an external doc search** (use `mcp__Ref__ref_search_documentation` or `mcp__Ref__ref_read_url`):
- You're flagging a version-specific API concern (e.g., "this method was deprecated in React 19") and the reference files don't cover it
- You're unsure whether a library/framework behaves the way you think it does
- The finding is `[blocking]` and hinges on framework-specific behavior you're not 100% certain about

Do NOT search docs for things you can verify from the code itself. Most findings should be verifiable by re-reading the source.

**Drop a finding if:**
- You can't point to the exact line that's wrong
- The surrounding context shows it's handled
- Your fix doesn't work with the codebase's patterns
- You're less than 90% confident after re-reading the code
- It's generic advice that doesn't reflect THIS specific code ("consider adding error handling" when you can't say WHAT error on WHAT line)

**Keep a finding if:**
- You can point to the exact line
- You verified the context doesn't handle it
- Your fix matches the codebase's patterns
- You'd bet money this is a real issue
- A reference file or linter confirms it

The goal is zero bullshit. Fewer verified findings beat many unverified ones. A review with 3 real catches earns trust. A review with 8 findings where 4 are wrong destroys it.

### Finding format

Every finding MUST include ALL of these:

1. **File:line** - exact location
2. **What** - the issue in one sentence
3. **Why** - what breaks in production (one sentence)
4. **Fix** - a concrete code change (actual code in a fenced block, not "consider doing X")
5. **Confidence** - only for `[blocking]` and `[important]`: state what you verified (e.g., "verified: no null check in callers X and Y", "verified: matches reference rule in react-19.md")

Findings without a fix are incomplete. If you genuinely cannot suggest a fix, explain exactly why.

Severity levels:
- `[blocking]` - will cause bugs, data loss, security vulnerabilities, or crashes in production. Must be verified against code context. Must have a working fix.
- `[important]` - significant correctness or quality concern, should fix before merge. Must be verified against code context.
- `[nit]` - style, naming, minor improvement. Self-evident from the code, no deep verification needed.
- `[suggestion]` - alternative approach worth considering. Explain the tradeoff, not just "consider X".

## Step 4: Deliver

Check report mode from prep output. **If mode is humanized, skip Standard Mode entirely - go directly to Humanized Mode below.**

**For PR mode**: generate the review internally, then go to PR Posting. Do NOT output the review separately before the preview.

### Standard Mode

```
## Change Walkthrough
<2-3 sentence summary>

### File Groups
- **auth/** - Updated login flow (3 files)

## Issues Found

### [blocking]
1. **file.ts:42** - Description. Impact.
   ```ts
   // Fix
   <corrected code>
   ```
   Source: <URL if from reference>

### [important] / [nit] / [suggestion]
...

## Test Suggestions
- <specific test cases for uncovered changes>
```

Omit empty sections. For complex changes (complexity > 0.5 or files > 5), include a Mermaid diagram:
```mermaid
graph LR
  A[file.ts] --> B[handler.ts] --> C[db.ts]
```

Zero issues on a trivial change (typo fix, config tweak) -> "No issues found."
Zero issues on a non-trivial change -> you didn't look hard enough. Go back to Step 3.

### Humanized Mode

PR review comments must look fully human-written. Your team lead flagged AI-augmented PR comments as a "significant HITM guardrail anti-pattern." Humanized mode is NOT standard mode with labels removed - it is a completely different voice.

**NEVER use in humanized mode**:
- Section headers (`##`, `###`, or any markdown heading)
- Bullet lists or numbered lists
- Severity labels (`[blocking]`, `[important]`, `[nit]`)
- Code block fences for fix suggestions (use inline backticks only)
- Summary tables or structured formatting of any kind
- The em dash character (the long unicode dash, U+2014) - use the regular hyphen-minus only
- Compliments, positive padding, or filler: "solid", "clean", "great", "nice", "looks good to ship", "no issues", "LGTM", "well done", "good job", "no security concerns"
- AI tells: "heads up", "nit:", "it's worth noting", "consider", "ensure", "I noticed that", "I'd suggest", "one thing to be aware of", "just worth knowing", "just be aware", "be aware", "worth a look", "worth noting", "is that the intent"
- Multi-clause compound sentences - keep sentences short, one or two per thought max

**ALWAYS in humanized mode**:
- Write in lowercase, casual prose. Short sentences. Paragraphs, not lists.
- Refer to code with inline backticks: `functionName`
- Ask questions naturally: "any reason to go with X here?"
- Combine related thoughts into the same paragraph - don't split related findings into separate comments
- Use the regular hyphen-minus character only. Never the long em dash (U+2014).
- Every sentence delivers information or asks a question. Nothing else.

**Example - WRONG** (AI tells, positive padding):

> solid addition for PII scrubbing. one thing to be aware of, the "auth" keyword will substring-match against "author". great for debugging user issues, just be aware it'll bump recording size. no blocking issues, clean typescript. looks good to ship.

**Example - RIGHT**:

> `BODY_DENY_KEYWORDS` has "auth" which substring-matches "author", "authority" etc - if you see over-redacted recordings later that's why. `PII_FIELDS` covers first/last name but not bare `name` so full names in a single field pass through unmasked. also `canvasFps: 4` in a logo editor means canvas frames in every recording - intentional? bumps recording size vs DOM-only.

**Example - WRONG** (structured, long, AI voice):

> ## Issues Found
> ### [blocking]
> 1. **auth.ts:42** - Missing input validation...

**Example - RIGHT**:

> auth.ts around line 42 - user input goes straight into the query without validation, sql injection risk. wrapping it in `sanitizeInput()` fixes it. the retry logic in `fetchData` doesn't backoff either so if upstream is down you'll hammer it.

Zero issues -> "looks clean, nothing jumped out." (Only on truly trivial changes. On non-trivial changes, find something.)

**PR mode**: generate review internally, go straight to PR Posting below. Do NOT show the review text separately before the preview.

### PR Posting

**If `Offer PR posting: no`** (or field missing): show review in conversation only. Skip to Wrap-up.

**If `Offer PR posting: yes`**: preview, confirm, post.

**1. Resolve format**: Check profile's `Posting format` field. If it says `single` or `inline`, use that. Only ask if missing:

> Post format? 1. Single comment 2. Inline comments 3. Skip

**2. Redact**: Scan all comment text for API keys, tokens, passwords, connection strings, private keys, `.env` values. Replace with `[REDACTED]`.

**3. Preview (MANDATORY)**:

For **inline mode**, show each comment with file:line target:
```
**file.ts:30**
> finding text here

**file.ts:49**
> another finding here
```

For **single comment mode**, show the full comment body.

Combine related findings touching the same concern into one comment.

**4. Confirm**: "post these? (yes / edit / skip)"

**STOP: Wait for confirmation before posting.**

**5. Post**:
- **Single**: `gh pr review <PR#> --comment --body "..."`
- **Inline**: `gh api repos/{owner}/{repo}/pulls/{pr}/comments --method POST -f body="<finding>" -f path="<file>" -f commit_id="$(gh pr view <PR#> --json headRefOid -q .headRefOid)" -F line=<line>` per finding.

**6. Labels**: `gh pr edit <PR#> --add-label "<labels>"` from prep output.

### Wrap-up

After delivering the review (and posting if PR mode), handle ALL post-delivery actions in **ONE** prompt. Do NOT split into multiple sequential stops.

In standard mode:
> Apply fixes? Dismiss findings? (numbers to fix, numbers to dismiss, or done)

In humanized mode:
> want me to fix any of these? anything to dismiss?

Then process the response:

1. **Apply fixes** with Edit tool if requested
2. **Record dismissals** to `feedback.jsonl`:
   ```json
   {"id": "<uuid>", "timestamp": "<ISO8601>", "type": "dismiss", "finding": "<desc>", "file_pattern": "<*.ext>", "reason": "<reason>"}
   ```
3. **Save review state** for PR/branch modes to `review-state.json`. **MUST Read then Edit, NEVER Write on existing file** (destroys other entries):
   ```json
   {"<mode>/<target>": {"last_reviewed_sha": "<HEAD>", "last_review_date": "<ISO8601>", "findings_count": N, "previous_findings": ["summary1"]}}
   ```
4. **Log to history** - append to `review-history.jsonl`:
   ```json
   {"timestamp": "<ISO8601>", "mode": "<mode>", "target": "<target>", "files_changed": N, "findings": {"blocking": N, "important": N, "nit": N, "suggestion": N}}
   ```

If user says "done" or "skip", still do steps 3 and 4 silently. The review is complete.
