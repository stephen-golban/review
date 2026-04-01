# Bug Patterns Reference

Check every changed file against these. Do not skip categories.

## Logic
- Off-by-one in loops, slice, substring, array access, pagination
- Wrong comparison (== vs ===, > vs >=, && vs ||)
- Missing break/return causing fallthrough
- Incorrect argument order in function calls
- Stale closures capturing variables by reference

## Null safety
- Optional chaining that assumes non-null downstream
- Nullable returns used without null checks
- Destructuring with missing properties
- Array access without bounds check

## Async and concurrency
- Missing await on async calls
- Race conditions from shared mutable state
- Promise.all with dependent mutations
- Unhandled promise rejections
- TOCTOU (check-then-act without atomicity)

## Resource management
- addEventListener without removeEventListener
- setInterval/setTimeout without cleanup
- Database connections not released on error paths
- Stream/file handles not closed in finally blocks
- Subscriptions (RxJS, event emitters) without unsubscribe

## Error handling
- Empty catch blocks or `.catch(() => {})`
- Catching too broadly (catch Exception vs specific types)
- Errors logged but not re-thrown or handled
- Missing error handling on external calls (API, DB, file I/O)
- Default values masking real failures

## Security
- SQL/NoSQL injection (string interpolation in queries)
- XSS (unescaped user input in HTML/templates)
- Path traversal (user input in file paths)
- Command injection (user input in shell commands)
- Auth bypass (missing auth checks on endpoints)
- Secrets in source code
- SSRF (user-controlled URLs in server-side requests)
- Prototype pollution (object spread/merge with user input)

## Performance
- N+1 queries (DB call inside a loop)
- Unbounded loops or recursion
- Missing pagination on list endpoints
- Blocking I/O on hot paths
- Unnecessary re-renders or recomputation

## Incomplete changes
- Imports of deleted/renamed modules
- Half-migrated patterns (old + new style mixed)
- Dead code left from refactor
- Missing updates to related files (types, tests, configs)
- Breaking changes to exported APIs without updating consumers

## Execution tracing

For each significant changed function:
1. **Inputs** - all possible shapes including adversarial
2. **Boundaries** - null, empty, zero, negative, NaN, too-large, malformed
3. **Concurrency** - called twice simultaneously? Rapidly?
4. **State** - consistent on error paths?
5. **Errors** - all paths handled? Caller sees what on failure?
6. **Resources** - released on ALL paths including error/early-return?
