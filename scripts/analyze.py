#!/usr/bin/env python3
"""
analyze.py - Code review analysis engine.

Extracts compact review packages from diffs. Replaces the need to read
entire source files into context. Outputs structured markdown with:
- Changed hunks with surrounding context
- Regex-based pattern scan warnings
- Cross-file dependency impact
- Reference file rule matches

Usage:
    python3 analyze.py --diff-cmd "git diff --cached" \
        --project-dir /path/to/project \
        --skill-dir /path/to/skill \
        [--context-lines 15] \
        [--output /path/to/output.md]

Requires only Python 3.8+ stdlib.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

class Hunk:
    __slots__ = (
        "old_start", "old_count", "new_start", "new_count",
        "header_ctx", "lines", "additions", "deletions",
    )

    def __init__(self, old_start: int, old_count: int, new_start: int,
                 new_count: int, header_ctx: str = ""):
        self.old_start = old_start
        self.old_count = old_count
        self.new_start = new_start
        self.new_count = new_count
        self.header_ctx = header_ctx
        self.lines: List[str] = []
        self.additions: List[Tuple[int, str]] = []
        self.deletions: List[Tuple[int, str]] = []


class FileDiff:
    __slots__ = ("path", "hunks", "is_new", "is_deleted", "is_binary")

    def __init__(self, path: str):
        self.path = path
        self.hunks: List[Hunk] = []
        self.is_new = False
        self.is_deleted = False
        self.is_binary = False


# ---------------------------------------------------------------------------
# Diff parsing
# ---------------------------------------------------------------------------

SKIP_EXTENSIONS = {
    ".lock", ".min.js", ".min.css", ".map", ".snap",
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg", ".woff", ".woff2",
    ".ttf", ".eot", ".pdf", ".zip", ".tar", ".gz",
}

SKIP_PATHS = {"dist/", "build/", "node_modules/", ".next/", "vendor/", "coverage/"}


def _should_skip(path: str) -> bool:
    ext = os.path.splitext(path)[1].lower()
    if ext in SKIP_EXTENSIONS:
        return True
    return any(path.startswith(p) or f"/{p}" in path for p in SKIP_PATHS)


def parse_diff(diff_text: str) -> List[FileDiff]:
    """Parse unified diff into structured FileDiffs."""
    files: List[FileDiff] = []
    cur_file: Optional[FileDiff] = None
    cur_hunk: Optional[Hunk] = None
    new_ln = old_ln = 0

    for line in diff_text.splitlines():
        if line.startswith("diff --git"):
            cur_hunk = None
            continue

        if line.startswith("+++ b/"):
            path = line[6:]
            if _should_skip(path):
                cur_file = None
                continue
            cur_file = FileDiff(path)
            files.append(cur_file)
            continue

        if line.startswith("+++ /dev/null"):
            if cur_file:
                cur_file.is_deleted = True
            continue
        if line.startswith("--- /dev/null"):
            if cur_file:
                cur_file.is_new = True
            continue
        if line.startswith("--- "):
            continue

        if line.startswith("Binary files"):
            if cur_file:
                cur_file.is_binary = True
            continue

        hm = re.match(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)", line)
        if hm and cur_file:
            cur_hunk = Hunk(
                int(hm.group(1)), int(hm.group(2) or "1"),
                int(hm.group(3)), int(hm.group(4) or "1"),
                hm.group(5).strip(),
            )
            cur_file.hunks.append(cur_hunk)
            new_ln = cur_hunk.new_start
            old_ln = cur_hunk.old_start
            continue

        if cur_hunk is not None and cur_file is not None:
            cur_hunk.lines.append(line)
            if line.startswith("+"):
                cur_hunk.additions.append((new_ln, line[1:]))
                new_ln += 1
            elif line.startswith("-"):
                cur_hunk.deletions.append((old_ln, line[1:]))
                old_ln += 1
            else:
                new_ln += 1
                old_ln += 1

    return files


# ---------------------------------------------------------------------------
# Context extraction
# ---------------------------------------------------------------------------

def extract_context(file_path: str, hunk: Hunk, ctx_lines: int,
                    project_dir: str) -> Optional[str]:
    """Read source and return hunk with surrounding context lines."""
    full = Path(project_dir) / file_path
    if not full.is_file():
        return None
    try:
        src_lines = full.read_text(errors="replace").splitlines()
    except Exception:
        return None

    start = max(0, hunk.new_start - ctx_lines - 1)
    end = min(len(src_lines), hunk.new_start + hunk.new_count + ctx_lines)

    added_set = {ln for ln, _ in hunk.additions}
    result: List[str] = []
    for i in range(start, end):
        num = i + 1
        marker = "+" if num in added_set else " "
        result.append(f"{num:4d} {marker} {src_lines[i]}")
    return "\n".join(result)


# ---------------------------------------------------------------------------
# Pattern scanning
# ---------------------------------------------------------------------------

# Language-agnostic patterns (applied to all files)
PATTERNS_COMMON: Dict[str, List[Tuple[str, str, float]]] = {
    "security": [
        (r"(password|secret|api_key|apikey|token|private_key)\s*[:=]\s*['\"][^'\"]{8,}['\"]",
         "possible hardcoded secret", 0.8),
    ],
}

# JS/TS patterns
PATTERNS_JS: Dict[str, List[Tuple[str, str, float]]] = {
    "security": [
        (r"innerHTML\s*=", "innerHTML assignment - XSS risk", 0.7),
        (r"dangerouslySetInnerHTML", "dangerouslySetInnerHTML - verify sanitization", 0.6),
        (r"eval\s*\(", "eval() - code injection risk", 0.9),
        (r"`[^`]*\$\{[^}]*(req|request|params|query|body|input|user)",
         "user input in template literal - injection risk", 0.8),
        (r"(exec|execSync|spawn)\s*\([^)]*\$\{",
         "command injection risk", 0.9),
    ],
    "error-handling": [
        (r"catch\s*\([^)]*\)\s*\{\s*\}", "empty catch block", 0.9),
        (r"\.catch\(\s*\(\s*\)\s*=>\s*\{\s*\}\s*\)", "empty .catch() handler", 0.9),
        (r"\.catch\(\s*\(\s*\)\s*=>\s*(null|undefined|void\s+0)\s*\)",
         "catch returning null - error masked", 0.7),
    ],
    "resource": [
        (r"addEventListener\s*\(", "event listener - check removeEventListener", 0.4),
        (r"setInterval\s*\(", "interval - check clearInterval cleanup", 0.6),
        (r"new\s+(WebSocket|EventSource)\s*\(",
         "persistent connection - check close()", 0.6),
        (r"\.(subscribe|on)\s*\(", "subscription - check unsubscribe/off", 0.35),
        (r"createConnection|createPool|connect\s*\(",
         "connection - check release/close", 0.5),
    ],
    "async": [
        (r"(?<!await\s)\b(fetch|axios\.\w+)\s*\(",
         "possibly missing await", 0.4),
        (r"\.then\s*\([^)]*\)\s*;?\s*$",
         ".then() without .catch()", 0.4),
    ],
    "performance": [
        (r"for\s*\([^)]*\)\s*\{[^}]*(await|\.query|\.findOne|\.exec|\.get|\.fetch)\b",
         "async/DB call in loop - N+1 risk", 0.7),
        (r"JSON\.parse\s*\(\s*JSON\.stringify\s*\(",
         "deep clone via JSON - slow for large objects", 0.5),
    ],
}

# Python patterns
PATTERNS_PYTHON: Dict[str, List[Tuple[str, str, float]]] = {
    "security": [
        (r"subprocess\.(call|run|Popen)\s*\([^)]*shell\s*=\s*True",
         "shell=True - command injection risk", 0.8),
        (r"pickle\.loads?\(", "pickle deserialization - arbitrary code execution risk", 0.7),
        (r"\beval\s*\(|\bexec\s*\(", "eval/exec - code injection risk", 0.9),
        (r"cursor\.execute\s*\([^)]*(%s|%d|\.format\(|f['\"])",
         "SQL injection via string formatting - use parameterized queries", 0.8),
        (r"yaml\.load\s*\([^)]*(?!Loader)", "yaml.load without SafeLoader - code execution risk", 0.7),
        (r"__import__\s*\(", "dynamic import - verify input is trusted", 0.6),
    ],
    "error-handling": [
        (r"except\s*:", "bare except catches SystemExit and KeyboardInterrupt", 0.7),
        (r"except\s+Exception\s*:", "broad except - consider specific exception types", 0.4),
        (r"except\s*.*:\s*\n\s*(pass|\.\.\.)\s*$", "silent exception swallowing", 0.8),
    ],
    "resource": [
        (r"open\s*\([^)]+\)(?!.*\bwith\b)", "file open without context manager - may leak handle", 0.5),
        (r"\.connect\s*\(", "connection opened - verify close/context manager", 0.4),
    ],
    "async": [
        (r"(?<!await\s)\basyncio\.\w+\s*\(", "possibly missing await on asyncio call", 0.5),
        (r"(?<!await\s)\b\w+\.async_\w+\s*\(", "possibly missing await on async method", 0.4),
    ],
    "performance": [
        (r"for\s+\w+\s+in\s+.*:\s*\n[^#]*\.(execute|fetchone|fetchall|query)\s*\(",
         "DB query in loop - N+1 risk", 0.7),
        (r"\+\s*=\s*.*\bin\s+(range|.*for)\b", "string concatenation in loop - use join()", 0.4),
    ],
}

# Go patterns
PATTERNS_GO: Dict[str, List[Tuple[str, str, float]]] = {
    "security": [
        (r"fmt\.Sprintf\s*\([^)]*%s[^)]*\).*(?:Query|Exec|Prepare)",
         "SQL injection via Sprintf - use parameterized query", 0.8),
        (r"http\.ListenAndServe\s*\(\s*\"", "plain HTTP listener - consider TLS", 0.5),
        (r"template\.HTML\s*\(", "unescaped HTML insertion - XSS risk", 0.7),
    ],
    "error-handling": [
        (r",\s*(?:err|_)\s*(?::?=)[^=].*\n(?!\s*if\s)", "error not checked after assignment", 0.6),
        (r"_\s*=\s*\w+\.\w+\(", "error explicitly ignored with _", 0.5),
    ],
    "resource": [
        (r"defer\s+\w+\.Close\(\)", "deferred close - verify error from Close is handled", 0.3),
        (r"\.Lock\(\)(?!.*defer.*Unlock)", "Lock without deferred Unlock", 0.6),
    ],
    "async": [
        (r"go\s+func\s*\(", "goroutine - verify no data race on shared variables", 0.4),
        (r"go\s+\w+\(", "goroutine launched - check for leaked goroutines", 0.35),
    ],
    "performance": [
        (r"for\s+.*range\s+.*\{[^}]*(\.Query|\.Exec|\.Get|\.Find)\s*\(",
         "DB call in range loop - N+1 risk", 0.7),
        (r"append\s*\(\s*\w+\s*,\s*\w+\.\.\.\s*\)", "append with spread in loop - consider pre-allocation", 0.4),
    ],
}

# Rust patterns
PATTERNS_RUST: Dict[str, List[Tuple[str, str, float]]] = {
    "security": [
        (r"unsafe\s*\{", "unsafe block - extra scrutiny required", 0.6),
        (r"std::mem::transmute", "transmute - verify type safety", 0.8),
        (r"from_raw_parts", "from_raw_parts - verify pointer validity and length", 0.7),
    ],
    "error-handling": [
        (r"\.unwrap\(\)", "unwrap panics on None/Err - use ? or handle explicitly", 0.5),
        (r"\.expect\s*\(", "expect panics with message - verify this can't fail in production", 0.4),
    ],
    "resource": [
        (r"Box::leak\s*\(", "Box::leak - intentional memory leak, verify cleanup", 0.7),
        (r"std::mem::forget\s*\(", "mem::forget - resource won't be dropped", 0.7),
    ],
    "async": [
        (r"\.block_on\s*\(", "block_on inside async context may deadlock", 0.6),
        (r"std::thread::spawn", "thread spawn - verify join or detach intent", 0.35),
    ],
}

# Java/Kotlin patterns
PATTERNS_JAVA: Dict[str, List[Tuple[str, str, float]]] = {
    "security": [
        (r"Statement\s*.*\.\s*execute\w*\s*\([^)]*\+",
         "SQL concatenation - use PreparedStatement", 0.8),
        (r"Runtime\.getRuntime\(\)\.exec\s*\(",
         "command execution - verify input sanitization", 0.8),
        (r"ObjectInputStream\s*\(", "deserialization - verify trusted source", 0.7),
        (r"new\s+Random\s*\(\)", "java.util.Random not cryptographically secure - use SecureRandom", 0.5),
    ],
    "error-handling": [
        (r"catch\s*\(\s*Exception\s+\w+\s*\)\s*\{\s*\}", "empty catch block", 0.9),
        (r"catch\s*\(\s*Throwable\s", "catching Throwable - too broad", 0.7),
        (r"\.printStackTrace\s*\(\s*\)", "printStackTrace in production - use proper logging", 0.6),
    ],
    "resource": [
        (r"new\s+(FileInputStream|FileOutputStream|Connection|Socket)\s*\(",
         "resource opened - verify try-with-resources or finally close", 0.5),
        (r"DriverManager\.getConnection\s*\(",
         "raw connection - verify close in finally block", 0.6),
    ],
}

# Map file extensions to pattern sets
_EXT_TO_PATTERNS: Dict[str, Dict[str, List[Tuple[str, str, float]]]] = {
    ".js": PATTERNS_JS, ".jsx": PATTERNS_JS, ".ts": PATTERNS_JS, ".tsx": PATTERNS_JS,
    ".mjs": PATTERNS_JS, ".cjs": PATTERNS_JS,
    ".py": PATTERNS_PYTHON, ".pyw": PATTERNS_PYTHON,
    ".go": PATTERNS_GO,
    ".rs": PATTERNS_RUST,
    ".java": PATTERNS_JAVA, ".kt": PATTERNS_JAVA, ".kts": PATTERNS_JAVA,
}


def _get_patterns_for_file(file_path: str) -> Dict[str, List[Tuple[str, str, float]]]:
    """Get merged common + language-specific patterns for a file."""
    ext = os.path.splitext(file_path)[1].lower()
    lang_patterns = _EXT_TO_PATTERNS.get(ext, {})
    # Merge common patterns with language-specific ones
    merged: Dict[str, List[Tuple[str, str, float]]] = {}
    for cat, pats in PATTERNS_COMMON.items():
        merged[cat] = list(pats)
    for cat, pats in lang_patterns.items():
        if cat in merged:
            merged[cat].extend(pats)
        else:
            merged[cat] = list(pats)
    return merged


# Keep backward-compatible reference for any external callers
PATTERNS = PATTERNS_JS


def scan_patterns(additions: List[Tuple[int, str]],
                  file_path: str) -> List[Dict[str, Any]]:
    """Scan added lines for language-aware bug patterns."""
    patterns = _get_patterns_for_file(file_path)
    if not patterns:
        return []
    warnings: List[Dict[str, Any]] = []
    # Detect comment prefix for this language
    ext = os.path.splitext(file_path)[1].lower()
    comment_prefixes = ("//", "#")
    if ext in (".py", ".pyw", ".rb"):
        comment_prefixes = ("#",)
    elif ext in (".rs", ".go", ".java", ".kt", ".kts", ".js", ".jsx", ".ts", ".tsx"):
        comment_prefixes = ("//",)

    # Pattern to redact secret values in snippets
    secret_redact = re.compile(
        r"((?:password|secret|api_key|apikey|token|private_key|auth)\s*[:=]\s*)"
        r"['\"][^'\"]+['\"]"
    )

    for line_num, content in additions:
        stripped = content.strip()
        if not stripped or any(stripped.startswith(p) for p in comment_prefixes):
            continue
        for category, pats in patterns.items():
            for regex, desc, conf in pats:
                if re.search(regex, content):
                    # Redact any secret values from snippet before including
                    safe_snippet = secret_redact.sub(
                        r'\1"[REDACTED]"', stripped[:120]
                    )
                    warnings.append({
                        "file": file_path,
                        "line": line_num,
                        "category": category,
                        "description": desc,
                        "confidence": conf,
                        "snippet": safe_snippet,
                    })
    return warnings


# ---------------------------------------------------------------------------
# Cross-file impact
# ---------------------------------------------------------------------------

def find_exports(file_path: str, project_dir: str) -> List[str]:
    """Extract exported names from a source file."""
    full = Path(project_dir) / file_path
    if not full.is_file():
        return []
    try:
        content = full.read_text(errors="replace")
    except Exception:
        return []

    exports: List[str] = []
    # JS/TS
    for m in re.finditer(
        r"export\s+(?:default\s+)?(?:function|class|const|let|var|type|interface|enum)\s+(\w+)",
        content,
    ):
        exports.append(m.group(1))
    for m in re.finditer(r"export\s*\{\s*([^}]+)\s*\}", content):
        for name in m.group(1).split(","):
            n = name.strip().split(" as ")[0].strip()
            if n:
                exports.append(n)
    # Python
    for m in re.finditer(r"^(?:def|class)\s+(\w+)", content, re.MULTILINE):
        exports.append(m.group(1))
    # Go
    for m in re.finditer(r"^func\s+(\w+)", content, re.MULTILINE):
        if m.group(1)[0].isupper():
            exports.append(m.group(1))
    return exports


def _exports_changed(fd: FileDiff) -> bool:
    """Check if the file's exported API surface changed (not just internals)."""
    export_patterns = [
        r"export\s+", r"module\.exports", r"exports\.",  # JS/TS
        r"^(?:def|class|async\s+def)\s+\w+",  # Python (top-level = public)
        r"^func\s+[A-Z]", r"^type\s+[A-Z]",  # Go (uppercase = exported)
        r"^pub\s+(?:fn|struct|enum|trait|type|mod|const)\s+",  # Rust
    ]
    combined = re.compile("|".join(export_patterns))
    for hunk in fd.hunks:
        for _, line in hunk.additions:
            if combined.search(line):
                return True
        for _, line in hunk.deletions:
            if combined.search(line):
                return True
    return False


def _import_grep_pattern(module: str, file_path: str) -> Optional[str]:
    """Build a language-specific import pattern for git grep -E."""
    ext = os.path.splitext(file_path)[1].lower()
    # Escape module name for regex
    mod = re.escape(module)

    if ext in (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"):
        # Match: import ... from '.../<module>', require('.../<module>')
        return f"(import\\s.*from\\s+['\"].*/?{mod}['\"]|require\\(['\"].*/?{mod}['\"])"
    elif ext in (".py", ".pyw"):
        # Match: from <module> import, import <module>
        return f"(from\\s+{mod}\\s+import|import\\s+{mod}\\b)"
    elif ext == ".go":
        # Match: ".../<module>"
        return f'"{mod}"'
    elif ext == ".rs":
        # Match: use ...::module, mod module
        return f"(use\\s+.*::{mod}|mod\\s+{mod}\\b)"
    elif ext in (".java", ".kt", ".kts"):
        # Match: import ....<module>
        return f"import\\s+.*\\.{mod}\\b"
    elif ext == ".rb":
        # Match: require '.../<module>', require_relative
        return f"require.*['\"].*/?{mod}['\"]"
    return None


def find_cross_file_impact(file_diffs: List[FileDiff],
                           project_dir: str) -> List[Dict[str, Any]]:
    """Find files importing changed modules that aren't in the changeset.

    Only searches when the exported API surface actually changed, and uses
    language-specific import patterns to reduce false positives.
    """
    changed = {fd.path for fd in file_diffs}
    impacts: List[Dict[str, Any]] = []

    for fd in file_diffs:
        if fd.is_deleted or fd.is_binary:
            continue
        # Only bother with cross-file if exports changed
        if not _exports_changed(fd):
            continue
        exports = find_exports(fd.path, project_dir)
        if not exports:
            continue

        module = Path(fd.path).stem
        pattern = _import_grep_pattern(module, fd.path)

        if pattern:
            # Use language-specific import pattern
            cmd = ["git", "grep", "-lE", "--", pattern]
        else:
            # Fallback: coarse search for unknown languages
            cmd = ["git", "grep", "-l", "--", module]

        try:
            r = subprocess.run(
                cmd, capture_output=True, text=True,
                cwd=project_dir, timeout=10,
            )
            if r.returncode == 0 and r.stdout.strip():
                importers = [
                    f.strip() for f in r.stdout.strip().splitlines()
                    if f.strip() not in changed and f.strip() != fd.path
                ]
                if importers:
                    impacts.append({
                        "changed_file": fd.path,
                        "exports": exports[:10],
                        "imported_by": importers[:10],
                    })
        except Exception:
            continue
    return impacts


# ---------------------------------------------------------------------------
# Deletion analysis and signature change detection
# ---------------------------------------------------------------------------

# Patterns that identify exported/public symbols being deleted
_EXPORT_DEL_PATTERNS = [
    # JS/TS
    (r"export\s+(?:default\s+)?(?:function|class|const|let|var|type|interface|enum)\s+(\w+)",
     {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"}),
    (r"export\s*\{([^}]+)\}",
     {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"}),
    # Python (top-level defs are public)
    (r"^(?:def|class|async\s+def)\s+(\w+)",
     {".py", ".pyw"}),
    # Go (uppercase = exported)
    (r"^func\s+([A-Z]\w*)", {".go"}),
    (r"^type\s+([A-Z]\w*)", {".go"}),
    # Rust
    (r"^pub\s+(?:fn|struct|enum|trait|type|const)\s+(\w+)", {".rs"}),
    # Java/Kotlin
    (r"^(?:public|protected)\s+(?:static\s+)?(?:class|interface|enum|void|int|String|\w+)\s+(\w+)",
     {".java", ".kt", ".kts"}),
]


def find_removed_exports(file_diffs: List[FileDiff],
                         project_dir: str) -> List[Dict[str, Any]]:
    """Detect removed/renamed exports and find consumers not in changeset."""
    changed = {fd.path for fd in file_diffs}
    removed: List[Dict[str, Any]] = []

    for fd in file_diffs:
        if fd.is_binary or fd.is_new:
            continue
        ext = os.path.splitext(fd.path)[1].lower()

        # Collect deleted symbol names from this file
        deleted_names: Set[str] = set()
        added_names: Set[str] = set()

        for hunk in fd.hunks:
            for _, line in hunk.deletions:
                for pattern, exts in _EXPORT_DEL_PATTERNS:
                    if ext not in exts:
                        continue
                    m = re.search(pattern, line)
                    if m:
                        # Handle grouped exports: export { a, b, c }
                        text = m.group(1)
                        for name in text.split(","):
                            n = name.strip().split(" as ")[0].strip()
                            if n and len(n) > 1:
                                deleted_names.add(n)
            for _, line in hunk.additions:
                for pattern, exts in _EXPORT_DEL_PATTERNS:
                    if ext not in exts:
                        continue
                    m = re.search(pattern, line)
                    if m:
                        text = m.group(1)
                        for name in text.split(","):
                            n = name.strip().split(" as ")[0].strip()
                            if n and len(n) > 1:
                                added_names.add(n)

        # Only flag names that were deleted but NOT re-added (true removals)
        truly_removed = deleted_names - added_names
        if not truly_removed:
            continue

        # Find consumers of removed symbols
        for name in list(truly_removed)[:5]:  # Cap to avoid excessive git grep
            try:
                r = subprocess.run(
                    ["git", "grep", "-l", "--", name],
                    capture_output=True, text=True,
                    cwd=project_dir, timeout=10,
                )
                if r.returncode == 0 and r.stdout.strip():
                    consumers = [
                        f.strip() for f in r.stdout.strip().splitlines()
                        if f.strip() not in changed and f.strip() != fd.path
                    ]
                    if consumers:
                        removed.append({
                            "file": fd.path,
                            "symbol": name,
                            "consumers": consumers[:10],
                        })
            except Exception:
                continue

    return removed


# Patterns to extract function signatures
_SIGNATURE_PATTERNS: List[Tuple[re.Pattern, Set[str]]] = [
    # JS/TS: function foo(a, b) or const foo = (a, b) =>
    (re.compile(r"(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*\(([^)]*)\)"),
     {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"}),
    (re.compile(r"(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s+)?\(([^)]*)\)"),
     {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"}),
    # Python
    (re.compile(r"(?:async\s+)?def\s+(\w+)\s*\(([^)]*)\)"),
     {".py", ".pyw"}),
    # Go
    (re.compile(r"func\s+(?:\([^)]*\)\s+)?(\w+)\s*\(([^)]*)\)"),
     {".go"}),
    # Rust
    (re.compile(r"(?:pub\s+)?(?:async\s+)?fn\s+(\w+)\s*\(([^)]*)\)"),
     {".rs"}),
    # Java/Kotlin
    (re.compile(r"(?:public|private|protected)\s+(?:static\s+)?(?:\w+\s+)?(\w+)\s*\(([^)]*)\)"),
     {".java", ".kt", ".kts"}),
]


def detect_signature_changes(file_diffs: List[FileDiff],
                             project_dir: str) -> List[Dict[str, Any]]:
    """Detect function signature changes (params added, removed, reordered)."""
    changed_paths = {fd.path for fd in file_diffs}
    sig_changes: List[Dict[str, Any]] = []

    for fd in file_diffs:
        if fd.is_binary or fd.is_new or fd.is_deleted:
            continue
        ext = os.path.splitext(fd.path)[1].lower()

        # Extract old and new signatures from hunks
        old_sigs: Dict[str, str] = {}  # name -> params string
        new_sigs: Dict[str, str] = {}

        for hunk in fd.hunks:
            for _, line in hunk.deletions:
                for pattern, exts in _SIGNATURE_PATTERNS:
                    if ext not in exts:
                        continue
                    m = pattern.search(line)
                    if m:
                        old_sigs[m.group(1)] = m.group(2).strip()
            for _, line in hunk.additions:
                for pattern, exts in _SIGNATURE_PATTERNS:
                    if ext not in exts:
                        continue
                    m = pattern.search(line)
                    if m:
                        new_sigs[m.group(1)] = m.group(2).strip()

        # Find functions whose signatures changed
        for name in old_sigs:
            if name in new_sigs and old_sigs[name] != new_sigs[name]:
                # Signature changed - check for callers not in changeset
                callers: List[str] = []
                try:
                    r = subprocess.run(
                        ["git", "grep", "-lE", "--", f"{name}\\s*\\("],
                        capture_output=True, text=True,
                        cwd=project_dir, timeout=10,
                    )
                    if r.returncode == 0 and r.stdout.strip():
                        callers = [
                            f.strip() for f in r.stdout.strip().splitlines()
                            if f.strip() not in changed_paths and f.strip() != fd.path
                        ]
                except Exception:
                    pass

                sig_changes.append({
                    "file": fd.path,
                    "function": name,
                    "old_params": old_sigs[name],
                    "new_params": new_sigs[name],
                    "callers_outside_changeset": callers[:10],
                })

    return sig_changes


# ---------------------------------------------------------------------------
# Reference matching
# ---------------------------------------------------------------------------

LANG_MAP: Dict[str, str] = {
    ".ts": "typescript", ".tsx": "typescript", ".js": "javascript",
    ".jsx": "javascript", ".py": "python", ".rs": "rust",
    ".go": "go", ".java": "java", ".rb": "ruby",
    ".php": "php", ".dart": "dart", ".swift": "swift",
}


def match_references(skill_dir: str,
                     file_diffs: List[FileDiff]) -> List[Dict[str, str]]:
    """Search reference files for rules relevant to changeset."""
    ref_dir = Path(skill_dir) / "reference"
    if not ref_dir.is_dir():
        return []

    languages: Set[str] = set()
    keywords: Set[str] = set()
    for fd in file_diffs:
        ext = os.path.splitext(fd.path)[1].lower()
        if ext in LANG_MAP:
            languages.add(LANG_MAP[ext])
        for hunk in fd.hunks:
            for _, line in hunk.additions:
                for m in re.finditer(
                    r"(?:import|require|from)\s+['\"]([^'\"]+)['\"]", line
                ):
                    keywords.add(m.group(1).split("/")[0].replace("@", "").lower())

    matches: List[Dict[str, str]] = []
    for ref_file in sorted(ref_dir.glob("*.md")):
        name_lower = ref_file.stem.lower().replace("-", " ").replace("_", " ")
        relevant = any(lang in name_lower for lang in languages)
        relevant = relevant or any(kw in name_lower for kw in keywords if len(kw) > 2)
        if not relevant:
            continue
        try:
            content = ref_file.read_text(errors="replace")
            for i, line in enumerate(content.splitlines(), 1):
                ls = line.strip()
                if ls.startswith("- ") or re.match(r"\d+\.\s", ls):
                    rule = ls.lstrip("- ").lstrip("0123456789. ")
                    if len(rule) > 10:
                        matches.append({"rule": rule, "source": f"{ref_file.name}:{i}"})
        except Exception:
            continue
    return matches[:20]


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------

def format_output(
    file_diffs: List[FileDiff],
    contexts: Dict[str, List[Optional[str]]],
    warnings: List[Dict[str, Any]],
    impacts: List[Dict[str, Any]],
    ref_matches: List[Dict[str, str]],
    removed_exports: Optional[List[Dict[str, Any]]] = None,
    sig_changes: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """Format review package as structured markdown."""
    out: List[str] = []

    total_add = sum(len(h.additions) for fd in file_diffs for h in fd.hunks)
    total_del = sum(len(h.deletions) for fd in file_diffs for h in fd.hunks)
    n_files = len([f for f in file_diffs if not f.is_binary])
    n_hunks = sum(len(f.hunks) for f in file_diffs)

    out.append("# Review Package\n")
    out.append(f"**Files**: {n_files} | **Hunks**: {n_hunks} | **Lines**: +{total_add}/-{total_del}\n")

    # -- Pattern warnings (high confidence) ---------------------------------
    high = [w for w in warnings if w["confidence"] >= 0.6]
    if high:
        out.append("## Pattern Scan Warnings\n")
        out.append("Auto-detected. Verify each against surrounding context.\n")
        for w in sorted(high, key=lambda x: -x["confidence"]):
            out.append(f"- **{w['file']}:{w['line']}** [{w['category']}] "
                       f"{w['description']} ({w['confidence']})")
            out.append(f"  `{w['snippet']}`")
        out.append("")

    # -- Cross-file impact --------------------------------------------------
    if impacts:
        out.append("## Cross-File Impact\n")
        out.append("Changed modules imported by files NOT in this changeset:\n")
        for ci in impacts:
            imp = ", ".join(ci["imported_by"][:5])
            more = f" (+{len(ci['imported_by']) - 5})" if len(ci["imported_by"]) > 5 else ""
            out.append(f"- **{ci['changed_file']}** -> {imp}{more}")
        out.append("")

    # -- Removed exports (breaking changes) ----------------------------------
    if removed_exports:
        out.append("## Removed Exports [BLOCKING]\n")
        out.append("Deleted symbols still referenced by files outside this changeset:\n")
        for re_item in removed_exports:
            consumers = ", ".join(re_item["consumers"][:5])
            more = f" (+{len(re_item['consumers']) - 5})" if len(re_item["consumers"]) > 5 else ""
            out.append(f"- **{re_item['file']}**: `{re_item['symbol']}` used by {consumers}{more}")
        out.append("")

    # -- Signature changes ---------------------------------------------------
    if sig_changes:
        out.append("## Signature Changes\n")
        out.append("Function signatures changed - callers may need updating:\n")
        for sc in sig_changes:
            out.append(f"- **{sc['file']}**: `{sc['function']}({sc['old_params']})` -> `{sc['function']}({sc['new_params']})`")
            if sc["callers_outside_changeset"]:
                callers = ", ".join(sc["callers_outside_changeset"][:5])
                out.append(f"  Callers NOT updated: {callers}")
            else:
                out.append(f"  All known callers are in this changeset")
        out.append("")

    # -- Reference matches --------------------------------------------------
    if ref_matches:
        out.append("## Relevant Reference Rules\n")
        for rm in ref_matches:
            out.append(f"- [{rm['source']}] {rm['rule']}")
        out.append("")

    # -- Per-file hunks with context ----------------------------------------
    out.append("## Changed Code\n")
    for fd in file_diffs:
        if fd.is_binary:
            out.append(f"### {fd.path} (binary)\n")
            continue
        if fd.is_deleted:
            out.append(f"### {fd.path} (deleted)\n")
            continue

        nadd = sum(len(h.additions) for h in fd.hunks)
        ndel = sum(len(h.deletions) for h in fd.hunks)
        out.append(f"### {fd.path} (+{nadd}/-{ndel})")

        # Low-confidence hints for this file
        low = [w for w in warnings if w["file"] == fd.path and w["confidence"] < 0.6]
        if low:
            hints = "; ".join(w["description"] for w in low[:3])
            out.append(f"*Hints: {hints}*")

        file_ctx = contexts.get(fd.path, [])
        ext = os.path.splitext(fd.path)[1].lstrip(".")
        lang_hint = {
            "ts": "typescript", "tsx": "tsx", "js": "javascript", "jsx": "jsx",
            "py": "python", "rs": "rust", "go": "go", "java": "java",
            "rb": "ruby", "php": "php",
        }.get(ext, "")

        for i, hunk in enumerate(fd.hunks):
            scope = f" `{hunk.header_ctx}`" if hunk.header_ctx else ""
            out.append(f"\n**Hunk {i + 1}** (lines {hunk.new_start}-"
                       f"{hunk.new_start + hunk.new_count}){scope}")

            ctx = file_ctx[i] if i < len(file_ctx) else None
            if ctx:
                out.append(f"```{lang_hint}")
                out.append(ctx)
                out.append("```")
            else:
                out.append("```diff")
                for dl in hunk.lines[:60]:
                    out.append(dl)
                if len(hunk.lines) > 60:
                    out.append(f"... ({len(hunk.lines) - 60} more lines)")
                out.append("```")

        out.append("")

    return "\n".join(out)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run_cmd(cmd: List[str], cwd: str, timeout: int = 30) -> Optional[str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd,
                           timeout=timeout)
        return r.stdout if r.returncode == 0 else None
    except Exception:
        return None


def main() -> None:
    ap = argparse.ArgumentParser(description="Code review analysis engine")
    ap.add_argument("--diff-cmd", default=None,
                    help="Diff command (space-separated)")
    ap.add_argument("--diff-file", default=None,
                    help="Path to pre-fetched diff text (avoids re-running diff)")
    ap.add_argument("--project-dir", default=".")
    ap.add_argument("--skill-dir", default=None)
    ap.add_argument("--context-lines", type=int, default=15)
    ap.add_argument("--output", default=None,
                    help="Output path (default: stdout)")
    ap.add_argument("--quick", action="store_true",
                    help="Quick mode: skip context extraction, cross-file impact, "
                         "and low-confidence patterns. For small/trivial changes.")
    args = ap.parse_args()

    project_dir = str(Path(args.project_dir).resolve())
    skill_dir = (str(Path(args.skill_dir).resolve()) if args.skill_dir
                 else str(Path(__file__).parent.parent.resolve()))

    # Get diff text: prefer pre-fetched file, fall back to running command
    diff_text: Optional[str] = None
    if args.diff_file:
        try:
            diff_text = Path(args.diff_file).read_text(errors="replace")
        except Exception:
            pass
    if not diff_text and args.diff_cmd:
        diff_text = run_cmd(args.diff_cmd.split(), project_dir, timeout=60)
    if not diff_text or not diff_text.strip():
        print("# Review Package\n\n**No diff output.**")
        sys.exit(0)

    file_diffs = parse_diff(diff_text)
    if not file_diffs:
        print("# Review Package\n\n**No changed files in diff.**")
        sys.exit(0)

    quick = args.quick

    # Extract context (skip in quick mode - use diff lines only)
    contexts: Dict[str, List[Optional[str]]] = {}
    if not quick:
        for fd in file_diffs:
            if fd.is_binary or fd.is_deleted:
                continue
            contexts[fd.path] = [
                extract_context(fd.path, h, args.context_lines, project_dir)
                for h in fd.hunks
            ]

    # Pattern scan (added lines only; quick mode = high confidence only)
    warnings: List[Dict[str, Any]] = []
    for fd in file_diffs:
        if fd.is_binary or fd.is_deleted:
            continue
        for hunk in fd.hunks:
            warnings.extend(scan_patterns(hunk.additions, fd.path))
    if quick:
        warnings = [w for w in warnings if w["confidence"] >= 0.7]

    # Cross-file impact (skip in quick mode)
    impacts = find_cross_file_impact(file_diffs, project_dir) if not quick else []

    # Removed exports - always run (catches breaking changes even in quick mode)
    removed_exports = find_removed_exports(file_diffs, project_dir)

    # Signature change detection (skip in quick mode)
    sig_changes = detect_signature_changes(file_diffs, project_dir) if not quick else []

    # Reference matching (skip in quick mode)
    ref_matches = match_references(skill_dir, file_diffs) if not quick else []

    # Format
    output = format_output(
        file_diffs, contexts, warnings, impacts, ref_matches,
        removed_exports=removed_exports if removed_exports else None,
        sig_changes=sig_changes if sig_changes else None,
    )

    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(output)
        print(f"Review package written to {args.output} "
              f"({len(file_diffs)} files, {len(warnings)} warnings)")
    else:
        print(output)


if __name__ == "__main__":
    main()
