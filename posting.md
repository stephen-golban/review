# PR Posting

**`Offer PR posting: no`** (or missing): show review in conversation only. Return to Wrap-up in review-flow.md.

**`Offer PR posting: yes`**: preview, confirm, post.

## 1. Resolve format

Check profile's `Posting format`. Only ask if missing:
> Post format? 1. Single comment 2. Inline comments 3. Skip

## 2. Redact (MANDATORY - run before preview)

Scan ALL content that will be posted - narrative text, code blocks, fix suggestions, inline comments, everything. No exceptions.

Target patterns:
- API keys, tokens, passwords, connection strings, private keys, auth headers
- Any string matching: `(password|secret|api_key|apikey|token|private_key|auth_token|bearer)\s*[:=]\s*["'][^"']+["']`
- AWS keys (`AKIA...`), GitHub tokens (`ghp_...`, `gho_...`), base64 JWT tokens
- `.env` variable values, database connection URIs with credentials

Replace matched values with `[REDACTED]`. Keep the key name visible, redact only the value.

If a fix code block contains a secret from the original source, redact it in the fix too. Never reproduce credential values in posted comments.

## 3. Preview (MANDATORY)

**Inline mode** - each comment with file:line:
```
**file.ts:30**
> finding text

**file.ts:49**
> another finding
```

**Single mode** - show full comment body.

Combine related findings on the same concern into one comment.

## 4. Confirm

"post these? (yes / edit / skip)"

**STOP: Wait for confirmation.**

## 5. Post

- **Single**: `gh pr review <PR#> --comment --body "..."`
- **Inline**: `gh api repos/{owner}/{repo}/pulls/{pr}/comments --method POST -f body="<finding>" -f path="<file>" -f commit_id="$(gh pr view <PR#> --json headRefOid -q .headRefOid)" -F line=<line>`

## 6. Labels

`gh pr edit <PR#> --add-label "<labels>"` from prep output.
