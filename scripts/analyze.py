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

PATTERNS: Dict[str, List[Tuple[str, str, float]]] = {
    "security": [
        (r"innerHTML\s*=", "innerHTML assignment - XSS risk", 0.7),
        (r"dangerouslySetInnerHTML", "dangerouslySetInnerHTML - verify sanitization", 0.6),
        (r"eval\s*\(", "eval() - code injection risk", 0.9),
        (r"`[^`]*\$\{[^}]*(req|request|params|query|body|input|user)",
         "user input in template literal - injection risk", 0.8),
        (r"(password|secret|api_key|apikey|token|private_key)\s*[:=]\s*['\"][^'\"]{8,}['\"]",
         "possible hardcoded secret", 0.8),
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


def scan_patterns(additions: List[Tuple[int, str]],
                  file_path: str) -> List[Dict[str, Any]]:
    """Scan added lines for common bug patterns."""
    warnings: List[Dict[str, Any]] = []
    for line_num, content in additions:
        stripped = content.strip()
        if not stripped or stripped.startswith("//") or stripped.startswith("#"):
            continue
        for category, pats in PATTERNS.items():
            for regex, desc, conf in pats:
                if re.search(regex, content):
                    warnings.append({
                        "file": file_path,
                        "line": line_num,
                        "category": category,
                        "description": desc,
                        "confidence": conf,
                        "snippet": stripped[:120],
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


def find_cross_file_impact(file_diffs: List[FileDiff],
                           project_dir: str) -> List[Dict[str, Any]]:
    """Find files importing changed modules that aren't in the changeset."""
    changed = {fd.path for fd in file_diffs}
    impacts: List[Dict[str, Any]] = []

    for fd in file_diffs:
        if fd.is_deleted or fd.is_binary:
            continue
        exports = find_exports(fd.path, project_dir)
        if not exports:
            continue
        module = Path(fd.path).stem
        try:
            r = subprocess.run(
                ["git", "grep", "-l", "--", module],
                capture_output=True, text=True, cwd=project_dir, timeout=10,
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
    ap.add_argument("--diff-cmd", required=True,
                    help="Diff command (space-separated)")
    ap.add_argument("--project-dir", default=".")
    ap.add_argument("--skill-dir", default=None)
    ap.add_argument("--context-lines", type=int, default=15)
    ap.add_argument("--output", default=None,
                    help="Output path (default: stdout)")
    args = ap.parse_args()

    project_dir = str(Path(args.project_dir).resolve())
    skill_dir = (str(Path(args.skill_dir).resolve()) if args.skill_dir
                 else str(Path(__file__).parent.parent.resolve()))

    # Run diff
    diff_text = run_cmd(args.diff_cmd.split(), project_dir, timeout=60)
    if not diff_text or not diff_text.strip():
        print("# Review Package\n\n**No diff output.**")
        sys.exit(0)

    file_diffs = parse_diff(diff_text)
    if not file_diffs:
        print("# Review Package\n\n**No changed files in diff.**")
        sys.exit(0)

    # Extract context
    contexts: Dict[str, List[Optional[str]]] = {}
    for fd in file_diffs:
        if fd.is_binary or fd.is_deleted:
            continue
        contexts[fd.path] = [
            extract_context(fd.path, h, args.context_lines, project_dir)
            for h in fd.hunks
        ]

    # Pattern scan (added lines only)
    warnings: List[Dict[str, Any]] = []
    for fd in file_diffs:
        if fd.is_binary or fd.is_deleted:
            continue
        for hunk in fd.hunks:
            warnings.extend(scan_patterns(hunk.additions, fd.path))

    # Cross-file impact
    impacts = find_cross_file_impact(file_diffs, project_dir)

    # Reference matching
    ref_matches = match_references(skill_dir, file_diffs)

    # Format
    output = format_output(file_diffs, contexts, warnings, impacts, ref_matches)

    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(output)
        print(f"Review package written to {args.output} "
              f"({len(file_diffs)} files, {len(warnings)} warnings)")
    else:
        print(output)


if __name__ == "__main__":
    main()
