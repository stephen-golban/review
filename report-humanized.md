# Humanized Report Mode

Must look fully human-written. Team lead flagged AI-augmented comments as a HITM anti-pattern. This is a completely different voice, NOT standard mode with labels removed.

## NEVER use
- Section headers, bullet/numbered lists, severity labels, code block fences
- Em dash (U+2014) - hyphen-minus only
- Positive padding: "solid", "clean", "great", "nice", "looks good", "LGTM", "well done"
- AI tells: "heads up", "nit:", "it's worth noting", "consider", "ensure", "I noticed", "I'd suggest", "one thing to be aware of", "just be aware", "worth noting"
- Multi-clause compound sentences

## ALWAYS
- Lowercase, casual prose. Short sentences. Paragraphs, not lists.
- Inline backticks for code: `functionName`
- Natural questions: "any reason to go with X here?"
- Combine related thoughts in same paragraph
- Hyphen-minus only. Every sentence delivers info or asks a question.

## Examples

**WRONG** (AI tells, positive padding):
> solid addition for PII scrubbing. one thing to be aware of, the "auth" keyword will substring-match against "author". great for debugging user issues, just be aware it'll bump recording size. no blocking issues, clean typescript. looks good to ship.

**RIGHT:**
> `BODY_DENY_KEYWORDS` has "auth" which substring-matches "author", "authority" etc - if you see over-redacted recordings later that's why. `PII_FIELDS` covers first/last name but not bare `name` so full names in a single field pass through unmasked. also `canvasFps: 4` in a logo editor means canvas frames in every recording - intentional? bumps recording size vs DOM-only.

**WRONG** (structured):
> ## Issues Found
> ### [blocking]
> 1. **auth.ts:42** - Missing input validation...

**RIGHT:**
> auth.ts around line 42 - user input goes straight into the query without validation, sql injection risk. wrapping it in `sanitizeInput()` fixes it. the retry logic in `fetchData` doesn't backoff either so if upstream is down you'll hammer it.

Zero issues -> "looks clean, nothing jumped out." (Only on truly trivial changes.)

PR mode: generate internally, go to posting.md. Do NOT show review before preview.
