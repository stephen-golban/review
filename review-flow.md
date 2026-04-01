# Review Flow

## Principles

1. **Context is currency.** Never dump raw diffs or full source files into context. Use analyze.py to extract compact review packages. Read only the 20-50 lines you need to verify a finding.
2. **Speed is respect.** Target 90 seconds. One script call for analysis. Zero unnecessary stops.
3. **Credibility is the product.** Three verified catches beat eight half-baked observations.
4. **Evolve.** Track what gets accepted vs dismissed. Suppress what doesn't land. Boost what does.

## Step 1: Prepare

```bash
python3 <skill-dir>/scripts/prep.py \
  --mode <mode> --target "<target>" --project-dir <project-root>
```

Read the prep output (small, stays in context). If "No changes found", tell user and stop.

## Step 2: Analyze

Run the analysis engine to extract a compact review package:

```bash
python3 <skill-dir>/scripts/analyze.py \
  --diff-cmd "<diff command from prep output>" \
  --project-dir <project-root> \
  --skill-dir <skill-dir> \
  --output /tmp/review-pkg-$$.md
```

Use `$$` (shell PID) or a timestamp to avoid collisions between parallel reviews. The file is deleted automatically in Wrap-up.

The script extracts: changed hunks with surrounding context, pattern scan warnings, cross-file impact, reference rule matches. It does the heavy data lifting so you don't bloat context with raw source.

### Adaptive context strategy

Check prep output's file count and total changed lines:

**Small changes** (< 200 changed lines AND < 5 files):
Read the analyze.py output directly. It's compact enough for main context.

**Large changes** (200+ lines OR 5+ files):
Launch an **Agent** to review the package:

> Read /tmp/review-pkg-<pid>.md. Also read the reference files listed below.
> Analyze every hunk for bugs, security issues, performance problems, incomplete changes.
> For each finding report: file:line, what, why, fix suggestion, severity.
> Be thorough but only report issues you're confident about after checking context.
> Return ONLY the findings list.

The Agent handles the bulk code. Main thread receives compact findings for verification.

### Linters and audits (conditional)

If prep output contains **"## Linters"**, run the detected linter:

| Linter | Command |
|---|---|
| eslint | `npx eslint --format json <files>` |
| biome | `npx biome check --reporter=json <files>` |
| ruff | `ruff check --output-format json <files>` |
| prettier | `prettier --check <files>` |
| clippy | `cargo clippy --message-format=json 2>&1` |
| golangci-lint | `golangci-lint run --out-format json <files>` |
| rubocop | `rubocop --format json <files>` |

If prep output contains **"## Dependency Changes Detected"**, run the audit:

| Dep File | Command |
|---|---|
| package.json/lock | `npm audit --json` |
| Cargo.toml/lock | `cargo audit --json` |
| requirements.txt | `pip-audit --format json` |
| go.mod | `govulncheck -json ./...` |

Run linter/audit in parallel with analyze.py using Agent if both apply. Skip silently if tools aren't installed.

## Step 3: Deep Review

You are hunting for bugs. Not confirming code compiles. Not rubber-stamping.

### Mindset

**Assume the code has defects. Find them.**

Do NOT talk yourself out of findings. Investigate. Verify against code. Flag if it holds up.

Zero issues on non-trivial change (>50 lines, multiple files, new logic) = you missed something. Go back.

But NEVER invent findings. Three real catches > eight where half are wrong.

### Investigation workflow

1. **Start from analyze.py output** - pattern warnings and cross-file impact are your starting points
2. **Read `patterns.md`** in this skill's directory for the full bug pattern checklist and execution tracing methodology
3. **Targeted Read for verification** - 20-50 lines around potential issues (offset + limit). Never full files.
4. **Grep for callers/consumers** - when flagging API changes or missing validation

### Cross-reference

1. **Blocking patterns** from profile - any match is `[blocking]`
2. **Priority focus areas** - extra scrutiny
3. **Reference rules** - from analyze.py output. Cite source links.
4. **Linter results** - deduplicate against your findings
5. **Dep vulnerabilities** - `[blocking]` if critical/high
6. **Learned patterns** from prep - respect dismissals, boost accepted categories
7. **Cross-file impact** - from analyze.py. Flag breaking API changes.
8. **Test gaps** from prep - flag missing coverage, suggest specific tests

### Verify (MANDATORY before presenting any finding)

Every finding must be defensible to a staff engineer who knows the codebase better than you.

**For EVERY finding:**
1. **Read actual lines** - targeted Read (20-50 lines). Confirm code does what you think.
2. **Check surrounding context** - guard clause? Comment? try/catch? Type constraint?
3. **Check callers** - Grep for actual callers. Maybe they already validate.
4. **Verify your fix** - uses APIs that exist here? Matches local patterns?
5. **Check references** - if a ref contradicts your finding, drop it.

**External doc search** (mcp__Ref tools, if available) only for:
- Version-specific API not in reference files
- Uncertain library/framework behavior
- `[blocking]` hinging on framework semantics

**DROP if:** can't point to exact line, context handles it, fix doesn't match patterns, <90% confident, generic advice.

**KEEP if:** exact line, verified context, fix matches patterns, you'd bet money, reference/linter confirms.

### Finding format

1. **File:line** - exact location
2. **What** - the issue (one sentence)
3. **Why** - production impact (one sentence)
4. **Fix** - concrete code (fenced block, not "consider doing X")
5. **Verified** - `[blocking]`/`[important]` only: what you checked

Severity:
- `[blocking]` - bugs, data loss, security, crashes. Verified. Working fix.
- `[important]` - correctness/quality. Verified.
- `[nit]` - style, naming. Self-evident.
- `[suggestion]` - alternative approach. Explain tradeoff.

## Step 4: Deliver

Check report mode from prep output.

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
   Verified: <what you checked>

### [important] / [nit] / [suggestion]
...

## Test Suggestions
- <specific test cases>
```

Omit empty sections. Mermaid diagram for complex changes (complexity > 0.5 or files > 5).

### Humanized Mode

Read `report-humanized.md` in this skill's directory. Follow those rules exclusively.

### PR Posting

Read `posting.md` in this skill's directory. Follow that workflow.

### Wrap-up

Handle ALL post-delivery actions in **ONE prompt**:

Standard: "Apply fixes? Dismiss findings? (numbers to fix, numbers to dismiss, or done)"
Humanized: "want me to fix any of these? anything to dismiss?"

Then:

**1. Apply fixes** with Edit if requested.

**2. Record feedback** to `feedback.jsonl`:
- Dismissed: `{"id": "<uuid>", "timestamp": "<ISO8601>", "type": "dismiss", "category": "<category>", "finding": "<desc>", "file_pattern": "<*.ext>", "reason": "<reason>"}`
- Accepted: `{"id": "<uuid>", "timestamp": "<ISO8601>", "type": "accept", "category": "<category>", "finding": "<desc>", "file_pattern": "<*.ext>"}`
- Categories: `logic`, `null-safety`, `async`, `resource`, `error-handling`, `security`, `performance`, `incomplete-change`, `test-gap`, `linter`, `dep-vuln`

**3. Save review state** for PR/branch to `review-state.json` (Read then Edit, NEVER Write on existing):
`{"<mode>/<target>": {"last_reviewed_sha": "<HEAD>", "last_review_date": "<ISO8601>", "findings_count": N, "accepted": N, "dismissed": N, "previous_findings": ["summary"]}}`

**4. Log** to `review-history.jsonl`:
`{"timestamp": "<ISO8601>", "mode": "<mode>", "target": "<target>", "files_changed": N, "findings": {"blocking": N, "important": N, "nit": N, "suggestion": N}, "accepted": N, "dismissed": N}`

**5. Evolve** (silent, automatic):
Read `feedback.jsonl`. If 3+ dismissals share same category AND file_pattern, Edit `project-profile.md` to add under "## Learned Suppressions":
`- Suppress <category> in <file_pattern> - dismissed N times (<reason>)`
If a category has >80% acceptance rate (min 5 data points), add under "## Learned Boosts":
`- Boost <category> in <file_pattern> - accepted N/M times`

**6. Cleanup** (silent, automatic):
- Delete the temp review package: `rm -f /tmp/review-pkg-*.md`
- Prune `feedback.jsonl` if over 200 lines: keep only the last 200 entries
- Prune `review-history.jsonl` if over 100 lines: keep only the last 100 entries
- Prune `review-state.json`: remove entries with `last_review_date` older than 90 days

If user says "done"/"skip", still do steps 3-6 silently.
