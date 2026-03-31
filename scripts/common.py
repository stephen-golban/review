#!/usr/bin/env python3
"""
common.py -- Shared utilities for the code review skill.

Contains file classification, diff parsing, risk analysis, complexity scoring,
test gap detection, change labeling, hunk header parsing, and file clustering.

This file is utilities only -- no CLI, no main(), no argparse.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple


# -- Section 1: File classification ----------------------------------------

LANG_MAP = {
    ".ts": "TypeScript", ".tsx": "TypeScript/React", ".js": "JavaScript",
    ".jsx": "JavaScript/React", ".mjs": "JavaScript", ".cjs": "JavaScript",
    ".py": "Python", ".rs": "Rust", ".go": "Go", ".rb": "Ruby",
    ".java": "Java", ".kt": "Kotlin", ".cs": "C#", ".swift": "Swift",
    ".dart": "Dart", ".ex": "Elixir", ".exs": "Elixir", ".php": "PHP",
    ".vue": "Vue", ".svelte": "Svelte",
    ".c": "C", ".cpp": "C++", ".cc": "C++", ".cxx": "C++",
    ".h": "C/C++", ".hpp": "C++", ".hh": "C++", ".hxx": "C++",
    ".sql": "SQL", ".graphql": "GraphQL", ".proto": "Protobuf",
    ".sh": "Shell", ".bash": "Shell",
    ".css": "CSS", ".scss": "SCSS", ".html": "HTML",
    ".md": "Markdown", ".json": "JSON", ".yaml": "YAML", ".yml": "YAML",
    ".toml": "TOML",
}

SECURITY_KEYWORDS = [
    "auth", "login", "password", "token", "secret", "session",
    "permission", "role", "crypto", "encrypt", "hash", "credential",
    "oauth", "jwt", "cors", "csrf", "sanitiz", "escape",
]

PERF_KEYWORDS = [
    "query", "cache", "index", "render", "batch", "bulk",
    "optimize", "perf", "slow", "latency", "throughput",
]

SKIP_DIRS: Set[str] = {
    "node_modules", ".git", "dist", "build", "target", "__pycache__",
    ".next", ".nuxt", "vendor", "venv", ".venv", "coverage", ".turbo",
}

TEST_PATTERNS = [r"test[_/]", r"_test\.", r"\.test\.", r"\.spec\.", r"tests/", r"__tests__/"]
CONFIG_PATTERNS = [r"\.env", r"config\.", r"\.json$", r"\.yaml$", r"\.yml$", r"\.toml$"]


def detect_language(filename: str) -> str:
    _, ext = os.path.splitext(filename)
    return LANG_MAP.get(ext.lower(), "Other")


def is_test_file(filename: str) -> bool:
    return any(re.search(p, filename.lower()) for p in TEST_PATTERNS)


def is_config_file(filename: str) -> bool:
    return any(re.search(p, filename.lower()) for p in CONFIG_PATTERNS)


def has_security_relevance(filename: str) -> bool:
    lower = filename.lower()
    return any(kw in lower for kw in SECURITY_KEYWORDS)


def has_perf_relevance(filename: str) -> bool:
    lower = filename.lower()
    return any(kw in lower for kw in PERF_KEYWORDS)


# -- Section 2: FileInfo, diff parsing -------------------------------------

class FileInfo:
    __slots__ = ("path", "language", "additions", "deletions",
                 "is_test", "is_config", "is_security", "is_perf")

    def __init__(self, path: str):
        self.path = path
        self.language = detect_language(path)
        self.additions = 0
        self.deletions = 0
        self.is_test = is_test_file(path)
        self.is_config = is_config_file(path)
        self.is_security = has_security_relevance(path)
        self.is_perf = has_perf_relevance(path)


def parse_diff_stats(diff_content: str) -> Dict[str, Tuple[int, int]]:
    """Parse unified diff, return {filename: (additions, deletions)}."""
    stats: Dict[str, Tuple[int, int]] = {}
    current_file: Optional[str] = None
    adds = dels = 0

    for line in diff_content.split("\n"):
        if line.startswith("diff --git"):
            if current_file:
                stats[current_file] = (adds, dels)
            match = re.search(r"b/(.+)$", line)
            current_file = match.group(1) if match else None
            adds = dels = 0
        elif current_file:
            if line.startswith("+") and not line.startswith("+++"):
                adds += 1
            elif line.startswith("-") and not line.startswith("---"):
                dels += 1

    if current_file:
        stats[current_file] = (adds, dels)
    return stats


def build_file_infos(
    file_list: Optional[List[str]] = None,
    diff_content: Optional[str] = None,
) -> List[FileInfo]:
    """Build FileInfo list from a file list, diff content, or both."""
    stats = parse_diff_stats(diff_content) if diff_content else {}
    if file_list is None:
        file_list = list(stats.keys())
    infos = []
    for path in file_list:
        fi = FileInfo(path)
        if path in stats:
            fi.additions, fi.deletions = stats[path]
        infos.append(fi)
    return infos


# -- Section 3: Risk analysis, scoring -------------------------------------

def identify_risks(files: List[FileInfo]) -> List[str]:
    risks: List[str] = []
    total = sum(f.additions + f.deletions for f in files)
    test_total = sum(f.additions + f.deletions for f in files if f.is_test)

    if total > 400:
        risks.append(f"Large change set ({total} lines)")
    if total > 800:
        risks.append("Consider splitting into smaller changes")

    if total > 50 and test_total == 0:
        risks.append("No test changes — verify test coverage")
    elif total > 100 and test_total / max(total, 1) < 0.2:
        risks.append(f"Low test ratio ({round(test_total / total * 100)}%)")

    sec_files = [f.path for f in files if f.is_security]
    if sec_files:
        names = ", ".join(sec_files[:3])
        extra = f" (+{len(sec_files) - 3} more)" if len(sec_files) > 3 else ""
        risks.append(f"Security-sensitive files: {names}{extra}")

    if any("migration" in f.path.lower() or f.language == "SQL" for f in files):
        risks.append("Database/migration changes detected")

    cfg = [f for f in files if f.is_config and not f.is_test]
    if cfg:
        risks.append(f"Configuration changes in {len(cfg)} file(s)")

    dirs = set(os.path.dirname(f.path) for f in files if not f.is_test and f.path)
    if len(dirs) >= 5:
        risks.append(f"Changes span {len(dirs)} directories — check architectural impact")

    return risks


def size_label(total: int) -> str:
    if total < 50:  return "XS"
    if total < 200: return "S"
    if total < 400: return "M"
    if total < 800: return "L"
    return "XL"


def complexity_score(files: List[FileInfo]) -> float:
    if not files:
        return 0.0
    total = sum(f.additions + f.deletions for f in files)
    size_f = min(total / 1000, 1.0)
    file_f = min(len(files) / 20, 1.0)
    test_lines = sum(f.additions + f.deletions for f in files if f.is_test)
    non_test_f = 1 - (test_lines / max(total, 1))
    langs = set(f.language for f in files if f.language != "Other")
    lang_f = min(len(langs) / 5, 1.0)
    return round(size_f * 0.4 + file_f * 0.2 + non_test_f * 0.2 + lang_f * 0.2, 2)


# -- Subprocess helper -----------------------------------------------------

def run_cmd(cmd: List[str], cwd: str, timeout: int = 30) -> Optional[str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd, timeout=timeout)
        return r.stdout.strip() if r.returncode == 0 else None
    except Exception:
        return None


# -- Section 4: Test gap analysis, labeling, clustering --------------------

# Maps language to test file naming conventions.
# {dir} = file's directory, {base} = filename without ext, {reldir} = relative path dirs
TEST_FILE_MAP: Dict[str, List[str]] = {
    "TypeScript":        ["{dir}/{base}.test.ts", "{dir}/{base}.spec.ts", "{dir}/__tests__/{base}.test.ts", "{dir}/__tests__/{base}.spec.ts"],
    "TypeScript/React":  ["{dir}/{base}.test.tsx", "{dir}/{base}.spec.tsx", "{dir}/__tests__/{base}.test.tsx"],
    "JavaScript":        ["{dir}/{base}.test.js", "{dir}/{base}.spec.js", "{dir}/__tests__/{base}.test.js"],
    "JavaScript/React":  ["{dir}/{base}.test.jsx", "{dir}/{base}.spec.jsx", "{dir}/__tests__/{base}.test.jsx"],
    "Python":            ["{dir}/test_{base}.py", "tests/test_{base}.py", "tests/{reldir}/test_{base}.py"],
    "Go":                ["{dir}/{base}_test.go"],
    "Ruby":              ["spec/{reldir}/{base}_spec.rb", "test/{reldir}/{base}_test.rb"],
    "Java":              ["src/test/java/{reldir}/{base}Test.java"],
    "Kotlin":            ["src/test/kotlin/{reldir}/{base}Test.kt"],
    "Rust":              [],  # Rust uses inline #[cfg(test)] modules
    "C#":                ["tests/{base}Tests.cs", "{dir}/{base}Tests.cs"],
    "PHP":               ["tests/{base}Test.php", "tests/{reldir}/{base}Test.php"],
    "Elixir":            ["test/{reldir}/{base}_test.exs"],
    "Dart":              ["test/{base}_test.dart", "test/{reldir}/{base}_test.dart"],
    "Swift":             ["Tests/{base}Tests.swift"],
}


def find_test_gaps(
    files: List[FileInfo],
    project_root: str,
    changed_set: Optional[Set[str]] = None,
) -> List[Dict[str, Any]]:
    """
    Identify changed source files that have no corresponding test file update.
    Returns list of {source, expected_tests[], has_existing_test}.
    """
    if changed_set is None:
        changed_set = {f.path for f in files}

    gaps: List[Dict[str, Any]] = []
    root = Path(project_root) if project_root else Path(".")

    for fi in files:
        # Skip test files, config files, non-code files
        if fi.is_test or fi.is_config or fi.language in ("Other", "Markdown", "JSON", "YAML", "TOML", "CSS", "SCSS", "HTML", "SQL", "Shell"):
            continue
        # Skip files with no additions (pure deletions)
        if fi.additions == 0:
            continue

        patterns = TEST_FILE_MAP.get(fi.language, [])
        if not patterns:
            continue

        d = os.path.dirname(fi.path)
        base = os.path.splitext(os.path.basename(fi.path))[0]
        # For Java/Kotlin, strip src/main/java or src/main/kotlin prefix
        reldir = d
        for prefix in ("src/main/java/", "src/main/kotlin/", "src/", "lib/", "app/"):
            if reldir.startswith(prefix):
                reldir = reldir[len(prefix):]
                break

        expected: List[str] = []
        for pat in patterns:
            test_path = pat.format(dir=d, base=base, reldir=reldir)
            expected.append(test_path)

        # Check if any expected test file was modified in this changeset
        test_in_changeset = any(t in changed_set for t in expected)
        if test_in_changeset:
            continue

        # Check if any expected test file exists on disk
        has_existing = any((root / t).is_file() for t in expected)

        gaps.append({
            "source": fi.path,
            "language": fi.language,
            "expected_tests": expected[:2],  # Show top 2 candidates
            "has_existing_test": has_existing,
        })

    return gaps


def classify_change_type(files: List[FileInfo]) -> List[str]:
    """Classify a changeset into PR labels based on file analysis heuristics."""
    labels: List[str] = []
    if not files:
        return labels

    source_files = [f for f in files if not f.is_test and not f.is_config and f.language not in ("Other", "Markdown", "JSON", "YAML", "TOML")]
    test_files = [f for f in files if f.is_test]
    config_files = [f for f in files if f.is_config and not f.is_test]
    doc_files = [f for f in files if f.language in ("Markdown",)]
    total_add = sum(f.additions for f in files)
    total_del = sum(f.deletions for f in files)

    # docs-only
    if doc_files and not source_files and not test_files:
        labels.append("docs")
        return labels

    # chore (config/infra only)
    if config_files and not source_files:
        labels.append("chore")
        return labels

    # Pure test additions
    if test_files and not source_files:
        labels.append("test")
        return labels

    # Feature detection: significant net additions, new files
    new_files = [f for f in source_files if f.deletions == 0 and f.additions > 5]
    if new_files and total_add > total_del * 2:
        labels.append("feature")

    # Bug fix heuristic: small change, modifies existing logic
    if not new_files and total_add < 50 and total_del > 0 and total_add <= total_del * 3:
        labels.append("bug-fix")

    # Refactor: similar add/delete ratio, renames, moves
    if source_files and abs(total_add - total_del) < max(total_add, total_del) * 0.3 and total_add > 20:
        labels.append("refactor")

    # Security
    sec_files = [f for f in files if f.is_security]
    if sec_files:
        labels.append("security")

    # Performance
    perf_files = [f for f in files if f.is_perf]
    if perf_files and len(perf_files) >= len(source_files) * 0.3:
        labels.append("performance")

    # Breaking change: deletions of exported symbols (heuristic)
    if total_del > 20 and total_del > total_add:
        labels.append("breaking-change")

    # Dependencies
    dep_files = {"package.json", "Cargo.toml", "requirements.txt", "go.mod", "Gemfile", "composer.json", "pyproject.toml"}
    if any(os.path.basename(f.path) in dep_files for f in files):
        labels.append("dependencies")

    # Deduplicate while preserving order
    seen: Set[str] = set()
    unique: List[str] = []
    for l in labels:
        if l not in seen:
            seen.add(l)
            unique.append(l)
    return unique


def parse_hunk_headers(diff_content: str) -> Dict[str, List[str]]:
    """
    Extract function/method names from diff hunk headers.
    Returns {filename: [function_names]}.
    """
    result: Dict[str, List[str]] = {}
    current_file: Optional[str] = None

    for line in diff_content.split("\n"):
        if line.startswith("diff --git"):
            m = re.search(r"b/(.+)$", line)
            current_file = m.group(1) if m else None
        elif line.startswith("@@") and current_file:
            # Hunk header format: @@ -old,count +new,count @@ context
            m = re.search(r"@@.*@@\s*(.*)", line)
            if m:
                ctx = m.group(1).strip()
                if ctx:
                    result.setdefault(current_file, [])
                    if ctx not in result[current_file]:
                        result[current_file].append(ctx)

    return result


DEP_FILE_NAMES: Set[str] = {
    "package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
    "Cargo.toml", "Cargo.lock", "requirements.txt", "setup.py",
    "pyproject.toml", "go.mod", "go.sum", "Gemfile", "Gemfile.lock",
    "composer.json", "composer.lock", "pubspec.yaml",
}


def has_dep_files(files: List[FileInfo]) -> List[str]:
    """Return dependency files present in the changeset."""
    return [f.path for f in files if os.path.basename(f.path) in DEP_FILE_NAMES]


def cluster_files(files: List[FileInfo]) -> List[Dict[str, Any]]:
    """
    Group files into logical clusters by directory and relationship.
    Returns list of {name, files[], description}.
    """
    by_dir: Dict[str, List[FileInfo]] = {}
    for f in files:
        d = os.path.dirname(f.path) or "root"
        by_dir.setdefault(d, []).append(f)

    clusters: List[Dict[str, Any]] = []
    for dirname, dir_files in sorted(by_dir.items(), key=lambda x: -len(x[1])):
        # Detect source+test pairs
        sources = [f for f in dir_files if not f.is_test and not f.is_config]
        tests = [f for f in dir_files if f.is_test]
        configs = [f for f in dir_files if f.is_config and not f.is_test]

        desc_parts: List[str] = []
        if sources:
            langs = set(f.language for f in sources if f.language != "Other")
            desc_parts.append(f"{len(sources)} source ({', '.join(sorted(langs)) if langs else 'misc'})")
        if tests:
            desc_parts.append(f"{len(tests)} test")
        if configs:
            desc_parts.append(f"{len(configs)} config")

        clusters.append({
            "name": dirname,
            "files": [f.path for f in dir_files],
            "description": ", ".join(desc_parts),
        })

    return clusters
