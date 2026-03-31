#!/usr/bin/env python3
"""
prep.py -- Review preparation orchestrator for the code review skill.

Handles profile parsing, reference matching, output formatting, and the CLI
entry point. Delegates file classification, diff parsing, risk analysis, and
related utilities to common.py.

Usage:
    python3 prep.py --mode <mode> [--target <value>] [options]

Modes:
    auto       Try staged -> unstaged -> branch (default)
    staged     Staged changes only
    unstaged   Unstaged working directory changes
    branch     Branch diff vs base
    pr         PR diff (target = PR# or URL)
    commit     Commit diff (target = SHA or SHA..SHA)
    file       Full file review (target = file path)
    dir        Directory review (target = directory path)

Options:
    --target VALUE       Mode-specific target
    --base BRANCH        Base branch override (default: from profile or 'main')
    --profile PATH       Path to project-profile.md
    --skill-dir PATH     Path to skill directory (default: parent of scripts/)
    --project-dir PATH   Project root (default: cwd)

Requires only Python 3.8+ stdlib.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).parent))
from common import (
    SKIP_DIRS, FileInfo, build_file_infos, classify_change_type,
    cluster_files, complexity_score, find_test_gaps, has_dep_files,
    identify_risks, parse_hunk_headers, run_cmd, size_label,
)


# -- Diff commands and auto-detection --------------------------------------

def get_diff_commands(mode: str, target: Optional[str], base: str) -> Tuple[List[str], List[str]]:
    if mode == "staged":
        return ["git", "diff", "--cached", "--name-only"], ["git", "diff", "--cached"]
    if mode == "unstaged":
        return ["git", "diff", "--name-only"], ["git", "diff"]
    if mode == "branch":
        ref = target or "HEAD"
        return (
            ["git", "diff", "--name-only", f"{base}...{ref}"],
            ["git", "diff", f"{base}...{ref}"],
        )
    if mode == "pr":
        return (
            ["gh", "pr", "diff", str(target), "--name-only"],
            ["gh", "pr", "diff", str(target)],
        )
    if mode == "commit":
        if target and ".." in target:
            return ["git", "diff", "--name-only", target], ["git", "diff", target]
        return (
            ["git", "show", "--name-only", "--format=", str(target)],
            ["git", "show", str(target)],
        )
    return [], []


def auto_detect_mode(cwd: str, base: str) -> Tuple[str, List[str], List[str]]:
    for mode in ("staged", "unstaged"):
        name_cmd, diff_cmd = get_diff_commands(mode, None, base)
        result = run_cmd(name_cmd, cwd)
        if result and result.strip():
            return mode, name_cmd, diff_cmd
    name_cmd, diff_cmd = get_diff_commands("branch", None, base)
    return "branch", name_cmd, diff_cmd


# -- Profile parsing -------------------------------------------------------

def parse_profile(path: Path) -> Dict[str, Any]:
    defaults: Dict[str, Any] = {
        "base_branch": "main",
        "blocking_patterns": [],
        "priorities": [],
        "report_mode": "standard",
        "pr_posting": False,
        "generated_refs": [],
    }
    if not path.is_file():
        return defaults

    content = path.read_text(encoding="utf-8", errors="replace")

    m = re.search(r"\*\*Base Branch\*\*:\s*(\S+)", content)
    if m:
        defaults["base_branch"] = m.group(1)

    m = re.search(r"\*\*Report mode\*\*:\s*(\S+)", content)
    if m:
        defaults["report_mode"] = m.group(1).lower()

    if re.search(r"\*\*Offer PR posting\*\*:\s*yes", content, re.IGNORECASE):
        defaults["pr_posting"] = True

    block_sec = re.search(r"## Blocking Patterns\n(.*?)(?=\n## |\Z)", content, re.DOTALL)
    if block_sec:
        for line in block_sec.group(1).strip().splitlines():
            line = line.strip()
            if line.startswith("- "):
                defaults["blocking_patterns"].append(line[2:])

    prio_sec = re.search(r"## Priority Focus Areas\n(.*?)(?=\n## |\Z)", content, re.DOTALL)
    if prio_sec:
        for line in prio_sec.group(1).strip().splitlines():
            line = line.strip()
            if re.match(r"\d+\.\s", line):
                defaults["priorities"].append(re.sub(r"^\d+\.\s*", "", line))

    for m in re.finditer(r"reference/(\S+\.md)", content):
        defaults["generated_refs"].append(m.group(1))

    return defaults


# -- Reference matching ----------------------------------------------------

def find_relevant_refs(
    skill_dir: Path, generated_refs: List[str], files: List[FileInfo]
) -> List[str]:
    """Return list of generated reference files relevant to the changed files."""
    if not generated_refs:
        return []

    # Collect languages present in the diff
    langs = set()
    for f in files:
        if f.language != "Other":
            langs.add(f.language.lower().split("/")[0])  # "TypeScript/React" -> "typescript"

    relevant = []
    ref_dir = skill_dir / "reference"
    for ref_name in generated_refs:
        ref_path = ref_dir / ref_name
        if not ref_path.is_file():
            continue
        # Match ref filename stem against detected languages
        stem = ref_path.stem.lower().replace("-", " ").replace("_", " ")
        # Always include if any language word appears in the filename
        if any(lang in stem for lang in langs):
            relevant.append(ref_name)
        # Also include if ref stem matches common framework names in the files
        elif _ref_matches_files(stem, files):
            relevant.append(ref_name)
        # If few refs exist, just include them all (cheap to read)
        elif len(generated_refs) <= 3:
            relevant.append(ref_name)

    return relevant


def _ref_matches_files(ref_stem: str, files: List[FileInfo]) -> bool:
    """Check if a reference file's topic matches any changed file paths."""
    keywords = ref_stem.split()
    return any(
        any(kw in f.path.lower() for kw in keywords)
        for f in files
    )


# -- Data file reading (inline) --------------------------------------------

def read_feedback(skill_dir: Path, files: List[FileInfo]) -> Optional[str]:
    """Read feedback.jsonl and return relevant learned patterns."""
    fb_path = skill_dir / "feedback.jsonl"
    if not fb_path.is_file():
        return None
    try:
        entries = []
        for line in fb_path.read_text(errors="replace").strip().splitlines():
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        if not entries:
            return None
        # Filter to relevant entries (match by file extension)
        file_exts = {os.path.splitext(f.path)[1] for f in files}
        relevant = []
        for e in entries:
            pattern = e.get("file_pattern", "")
            if not pattern or any(pattern.endswith(ext) or ext in pattern for ext in file_exts):
                relevant.append(e)
        if not relevant:
            return None
        lines = ["## Learned Patterns\n"]
        for e in relevant[:10]:
            action = e.get("type", "note")
            finding = e.get("finding", "")
            reason = e.get("reason", "")
            lines.append(f"- **{action}**: {finding} — {reason}")
        return "\n".join(lines)
    except Exception:
        return None


def read_review_state(skill_dir: Path, mode: str, target: Optional[str]) -> Optional[str]:
    """Read review-state.json for incremental review context."""
    if not target or mode not in ("pr", "branch"):
        return None
    state_path = skill_dir / "review-state.json"
    if not state_path.is_file():
        return None
    try:
        data = json.loads(state_path.read_text(errors="replace"))
        key = f"{mode}/{target}"
        entry = data.get(key)
        if not entry:
            return None
        sha = entry.get("last_reviewed_sha", "unknown")
        count = entry.get("findings_count", 0)
        findings = entry.get("previous_findings", [])
        lines = [f"## Previous Review\n"]
        lines.append(f"**Last reviewed at**: {sha} ({count} findings)")
        if findings:
            lines.append("Previous findings:")
            for f in findings[:5]:
                lines.append(f"- {f}")
        lines.append(f"\nUse incremental diff from `{sha}` to review only new changes.")
        return "\n".join(lines)
    except Exception:
        return None


# -- Output formatting -----------------------------------------------------

def format_output(
    mode: str,
    diff_cmd: List[str],
    files: List[FileInfo],
    risks: List[str],
    cplx: float,
    profile: Dict[str, Any],
    relevant_refs: List[str],
    commit_log: Optional[str] = None,
    test_gaps: Optional[List[Dict[str, Any]]] = None,
    labels: Optional[List[str]] = None,
    clusters: Optional[List[Dict[str, Any]]] = None,
    dep_files: Optional[List[str]] = None,
    hunk_fns: Optional[Dict[str, List[str]]] = None,
    feedback_output: Optional[str] = None,
    prev_review: Optional[str] = None,
    linters_detected: bool = False,
) -> str:
    total_add = sum(f.additions for f in files)
    total_del = sum(f.deletions for f in files)
    total = total_add + total_del
    sz = size_label(total)

    lang_counts: Dict[str, int] = {}
    for f in files:
        if f.language != "Other":
            lang_counts[f.language] = lang_counts.get(f.language, 0) + 1
    lang_str = ", ".join(
        f"{l} ({n})" for l, n in sorted(lang_counts.items(), key=lambda x: -x[1])
    )

    out: List[str] = []

    # -- Header ------------------------------------------------------------
    out.append("# Review Prep\n")
    out.append(f"**Mode**: {mode} | **Base**: {profile['base_branch']} | **Report**: {profile['report_mode']}")
    if total > 0:
        out.append(f"**Files**: {len(files)} | **Lines**: +{total_add}/-{total_del} ({total}) | **Size**: {sz} | **Complexity**: {cplx}")
    else:
        out.append(f"**Files**: {len(files)} (full file review, no diff)")
    if lang_str:
        out.append(f"**Languages**: {lang_str}")
    if labels:
        out.append(f"**Suggested labels**: {', '.join(labels)}")
    out.append("")

    # -- Previous review state (incremental) --------------------------------
    if prev_review:
        out.append(prev_review)
        out.append("")

    # -- File clusters ------------------------------------------------------
    if clusters and len(clusters) > 1:
        out.append("## Change Clusters\n")
        for c in clusters[:10]:
            out.append(f"- **{c['name']}/** — {c['description']}")
        out.append("")

    # -- File table ---------------------------------------------------------
    out.append("## Changed Files\n")
    out.append(f"{'File':<55} {'Lang':<18} {'+-':<12} Flags")
    out.append("-" * 95)
    for f in sorted(files, key=lambda x: (x.is_test, x.is_config, x.path)):
        flags = []
        if f.is_security: flags.append("security")
        if f.is_test:     flags.append("test")
        if f.is_config:   flags.append("config")
        if f.is_perf:     flags.append("perf")
        chg = f"+{f.additions}/-{f.deletions}" if f.additions or f.deletions else "-"
        out.append(f"{f.path:<55} {f.language:<18} {chg:<12} {', '.join(flags)}")
    out.append("")

    # -- Risks --------------------------------------------------------------
    if risks:
        out.append("## Risk Factors\n")
        for r in risks:
            out.append(f"- {r}")
        out.append("")

    # -- Test coverage gaps -------------------------------------------------
    if test_gaps:
        out.append("## Test Coverage Gaps\n")
        for gap in test_gaps[:8]:  # Cap to avoid bloat
            existing = " (test file exists, not updated)" if gap["has_existing_test"] else " (no test file found)"
            out.append(f"- **{gap['source']}**{existing}")
            if gap["expected_tests"]:
                out.append(f"  Expected: `{gap['expected_tests'][0]}`")
        out.append("")

    # -- Changed functions (from hunk headers) ------------------------------
    if hunk_fns:
        out.append("## Changed Functions\n")
        for fname, fns in list(hunk_fns.items())[:10]:
            for fn in fns[:5]:
                out.append(f"- `{fname}`: {fn}")
        out.append("")

    # -- Dependency changes -------------------------------------------------
    if dep_files:
        out.append("## Dependency Changes Detected\n")
        for df in dep_files:
            out.append(f"- `{df}`")
        out.append("")

    # -- Learned patterns (from feedback) -----------------------------------
    if feedback_output:
        out.append(feedback_output)
        out.append("")

    # -- Available linters --------------------------------------------------
    if linters_detected:
        out.append("## Linters\n")
        out.append("Project linter configuration detected. Run linting as part of review.")
        out.append("")

    # -- Profile context ----------------------------------------------------
    if profile["blocking_patterns"]:
        out.append("## Blocking Patterns (from profile)\n")
        for p in profile["blocking_patterns"]:
            out.append(f"- {p}")
        out.append("")

    if profile["priorities"]:
        out.append("## Priority Focus (from profile)\n")
        for i, p in enumerate(profile["priorities"], 1):
            out.append(f"{i}. {p}")
        out.append("")

    # -- Generated refs to load ---------------------------------------------
    if relevant_refs:
        out.append("## Read These References\n")
        for r in relevant_refs:
            out.append(f"- `reference/{r}`")
        out.append("")

    # -- Commit log ---------------------------------------------------------
    if commit_log:
        out.append("## Commit History\n")
        out.append("```")
        out.append(commit_log)
        out.append("```")
        out.append("")

    # -- Diff command -------------------------------------------------------
    if diff_cmd:
        out.append("## Diff Command\n")
        out.append("```")
        out.append(" ".join(diff_cmd))
        out.append("```")

    # -- Impact analysis hint -----------------------------------------------
    source_count = len([f for f in files if not f.is_test and not f.is_config and f.language not in ("Other", "Markdown")])
    if source_count > 1 and mode in ("branch", "pr", "commit"):
        out.append("")
        source_paths = " ".join(f.path for f in files if not f.is_test and not f.is_config and f.language not in ("Other", "Markdown"))
        out.append("## Cross-File Impact")
        out.append(f"Run: `python3 <skill-dir>/scripts/impact-analyzer.py --project-dir <project-root> --files {source_paths[:300]}`")

    return "\n".join(out)


# -- Main ------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Review preparation script")
    parser.add_argument(
        "--mode", default="auto",
        choices=["auto", "staged", "unstaged", "branch", "pr", "commit", "file", "dir"],
    )
    parser.add_argument("--target", default=None)
    parser.add_argument("--base", default=None)
    parser.add_argument("--profile", default=None)
    parser.add_argument("--skill-dir", default=None)
    parser.add_argument("--project-dir", default=".")
    args = parser.parse_args()

    skill_dir = Path(args.skill_dir).resolve() if args.skill_dir else Path(__file__).parent.parent.resolve()
    project_dir = Path(args.project_dir).resolve()

    profile_path = Path(args.profile) if args.profile else skill_dir / "project-profile.md"
    profile = parse_profile(profile_path)

    base = args.base or profile["base_branch"]
    profile["base_branch"] = base

    mode = args.mode
    target = args.target

    # -- File/Dir modes (no diff) -------------------------------------------
    if mode in ("file", "dir"):
        if not target:
            print("Error: --target required for file/dir mode", file=sys.stderr)
            sys.exit(1)

        target_path = Path(target)
        if not target_path.exists():
            target_path = project_dir / target
        if not target_path.exists():
            print(f"Error: {target} not found", file=sys.stderr)
            sys.exit(1)

        if mode == "file":
            try:
                rel = str(target_path.relative_to(project_dir))
            except ValueError:
                rel = str(target_path)
            file_list = [rel]
        else:
            file_list = []
            for dirpath, dirnames, filenames in os.walk(target_path):
                dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
                for fn in filenames:
                    if not fn.startswith("."):
                        full = Path(dirpath) / fn
                        try:
                            rel = str(full.relative_to(project_dir))
                        except ValueError:
                            rel = str(full)
                        file_list.append(rel)

        files = build_file_infos(file_list)
        risks: List[str] = []
        cplx = 0.0
        diff_cmd: List[str] = []
        commit_log = None
        diff_content = None

    # -- Diff-based modes ---------------------------------------------------
    else:
        if mode == "auto":
            mode, name_cmd, diff_cmd = auto_detect_mode(str(project_dir), base)
        else:
            name_cmd, diff_cmd = get_diff_commands(mode, target, base)

        file_list_str = run_cmd(name_cmd, str(project_dir))
        if not file_list_str or not file_list_str.strip():
            print("# Review Prep\n\n**No changes found.**")
            sys.exit(0)

        file_list = [f for f in file_list_str.strip().splitlines() if f.strip()]
        diff_content = run_cmd(diff_cmd, str(project_dir), timeout=60)

        files = build_file_infos(file_list, diff_content)
        risks = identify_risks(files)
        cplx = complexity_score(files)

        commit_log = None
        if mode == "branch":
            ref = target or "HEAD"
            log = run_cmd(["git", "log", "--oneline", f"{base}...{ref}"], str(project_dir))
            if log:
                commit_log = log

    # -- Match generated references -----------------------------------------
    relevant_refs = find_relevant_refs(
        skill_dir, profile.get("generated_refs", []), files
    )

    # -- Test gap analysis --------------------------------------------------
    test_gaps = find_test_gaps(files, str(project_dir)) if mode not in ("file", "dir") else []

    # -- Change type labels -------------------------------------------------
    labels = classify_change_type(files) if mode not in ("file", "dir") else []

    # -- File clustering ----------------------------------------------------
    clusters = cluster_files(files) if len(files) > 3 else []

    # -- Dependency file detection ------------------------------------------
    dep_file_list = has_dep_files(files)

    # -- Hunk header parsing (changed functions) ----------------------------
    hunk_fns: Optional[Dict[str, List[str]]] = None
    if diff_content:
        hunk_fns = parse_hunk_headers(diff_content)
        # Only include if we found meaningful function names
        if hunk_fns:
            hunk_fns = {k: v for k, v in hunk_fns.items() if v}

    # -- Load feedback (learned patterns) -- inline -------------------------
    feedback_output = read_feedback(skill_dir, files)

    # -- Load previous review state (incremental) -- inline -----------------
    prev_review = read_review_state(skill_dir, mode, target)

    # -- Detect available linters (config file check only) ------------------
    linter_configs = {
        "eslint": [".eslintrc*", "eslint.config.*"],
        "biome": ["biome.json", "biome.jsonc"],
        "ruff": ["ruff.toml"],
        "prettier": [".prettierrc*"],
        "clippy": ["Cargo.toml"],
        "golangci-lint": [".golangci.yml", ".golangci.yaml"],
        "rubocop": [".rubocop.yml"],
    }
    linters_detected = False
    for name, pats in linter_configs.items():
        for pat in pats:
            if list(project_dir.glob(pat)):
                linters_detected = True
                break
        if linters_detected:
            break

    # -- Output -------------------------------------------------------------
    output = format_output(
        mode=mode,
        diff_cmd=diff_cmd,
        files=files,
        risks=risks,
        cplx=cplx,
        profile=profile,
        relevant_refs=relevant_refs,
        commit_log=commit_log,
        test_gaps=test_gaps if test_gaps else None,
        labels=labels if labels else None,
        clusters=clusters if clusters else None,
        dep_files=dep_file_list if dep_file_list else None,
        hunk_fns=hunk_fns if hunk_fns else None,
        feedback_output=feedback_output,
        prev_review=prev_review,
        linters_detected=linters_detected,
    )
    print(output)


if __name__ == "__main__":
    main()
