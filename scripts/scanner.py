#!/usr/bin/env python3
"""
project-scanner.py — Deterministic project scanner for code review.

Scans a codebase and outputs structured JSON describing project structure,
tech stack, linting config, standards docs, CI/CD, file distribution, and git info.

Usage:
    python project-scanner.py [--path /path/to/project] [--pretty]

Defaults to current working directory. Requires only Python 3.8+ stdlib.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

sys.path.insert(0, str(Path(__file__).parent))
from detectors import run_all_detectors, _read_json, _read_text

SCANNER_VERSION = "1.0.0"

SKIP_DIRS: Set[str] = {
    "node_modules", ".git", "dist", "build", "target", "__pycache__",
    ".next", ".nuxt", "vendor", "venv", ".venv", ".tox", ".mypy_cache",
    ".pytest_cache", ".turbo", ".cache", "coverage", ".nx",
}


# ── Utility helpers ─────────────────────────────────────────────────────────

def _run(cmd: List[str], cwd: Path, timeout: int = 10) -> Optional[str]:
    """Run a subprocess, return stdout or None."""
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True, cwd=str(cwd), timeout=timeout,
        )
        if r.returncode == 0:
            return r.stdout.strip()
        return None
    except Exception:
        return None


def _glob_first(root: Path, patterns: List[str]) -> Optional[Path]:
    """Return the first matching file under root for any pattern."""
    for pat in patterns:
        for p in root.glob(pat):
            if p.is_file():
                return p
    return None


def _find_files(root: Path, patterns: List[str]) -> List[Path]:
    """Return all matching files under root for given glob patterns."""
    results: List[Path] = []
    for pat in patterns:
        results.extend(p for p in root.glob(pat) if p.is_file())
    return results


# ── Scanners ────────────────────────────────────────────────────────────────

class ProjectScanner:
    """Aggregates all detection logic and produces a JSON report."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.warnings: List[str] = []
        self.tech_stack: List[Dict[str, Any]] = []

    # ── helpers ──────────────────────────────────────────────────────────

    def _warn(self, msg: str) -> None:
        self.warnings.append(msg)

    def _add_tech(
        self, name: str, version: Optional[str], category: str,
        source: str, workspace: Optional[str] = None,
    ) -> None:
        entry: Dict[str, Any] = {
            "name": name, "version": version, "category": category,
            "source": source,
        }
        if workspace:
            entry["workspace"] = workspace
        self.tech_stack.append(entry)

    # ── project structure ────────────────────────────────────────────────

    def detect_project(self) -> Dict[str, Any]:
        project_name = self.root.name
        project_type = "single-project"
        workspaces: List[Dict[str, Any]] = []

        # Check monorepo indicators
        pnpm_ws = self.root / "pnpm-workspace.yaml"
        turbo = self.root / "turbo.json"
        nx = self.root / "nx.json"
        lerna = self.root / "lerna.json"
        root_pkg = self.root / "package.json"

        mono_dirs = ["apps", "packages", "services", "libs"]
        has_mono_dirs = any((self.root / d).is_dir() for d in mono_dirs)

        ws_paths: List[str] = []

        # pnpm-workspace.yaml
        if pnpm_ws.is_file():
            project_type = "monorepo"
            text = _read_text(pnpm_ws)
            if text:
                # Simple YAML parse for packages list
                for m in re.finditer(r"^\s*-\s*['\"]?([^'\"#\n]+)", text, re.MULTILINE):
                    ws_paths.append(m.group(1).strip())

        # package.json workspaces
        if root_pkg.is_file():
            pkg = _read_json(root_pkg)
            if pkg:
                if "name" in pkg:
                    project_name = pkg["name"]
                ws_field = pkg.get("workspaces")
                if ws_field:
                    project_type = "monorepo"
                    if isinstance(ws_field, list):
                        ws_paths.extend(ws_field)
                    elif isinstance(ws_field, dict) and "packages" in ws_field:
                        ws_paths.extend(ws_field["packages"])

        if turbo.is_file() or nx.is_file() or lerna.is_file():
            project_type = "monorepo"

        if has_mono_dirs and project_type == "single-project":
            # Heuristic: if top-level mono dirs exist, treat as monorepo
            project_type = "monorepo"

        # Resolve workspace globs into concrete paths
        if ws_paths:
            workspaces = self._resolve_workspaces(ws_paths)
        elif project_type == "monorepo":
            # Fallback: enumerate known mono dirs
            for d in mono_dirs:
                base = self.root / d
                if base.is_dir():
                    for child in sorted(base.iterdir()):
                        if child.is_dir() and child.name not in SKIP_DIRS:
                            ws_info = self._describe_workspace(child, f"{d}/{child.name}")
                            workspaces.append(ws_info)

        return {
            "name": project_name,
            "path": str(self.root),
            "type": project_type,
            "workspaces": workspaces,
        }

    def _resolve_workspaces(self, patterns: List[str]) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        seen: Set[str] = set()
        for pat in patterns:
            # Expand simple globs like "packages/*"
            pat_clean = pat.rstrip("/")
            base_parts = pat_clean.split("/")
            if "*" in base_parts[-1]:
                # Directory glob
                parent = self.root / "/".join(base_parts[:-1]) if len(base_parts) > 1 else self.root
                if parent.is_dir():
                    for child in sorted(parent.iterdir()):
                        if child.is_dir() and child.name not in SKIP_DIRS:
                            rel = str(child.relative_to(self.root))
                            if rel not in seen:
                                seen.add(rel)
                                results.append(self._describe_workspace(child, rel))
            else:
                # Exact path
                ws_dir = self.root / pat_clean
                if ws_dir.is_dir():
                    rel = str(ws_dir.relative_to(self.root))
                    if rel not in seen:
                        seen.add(rel)
                        results.append(self._describe_workspace(ws_dir, rel))
        return results

    def _describe_workspace(self, ws_dir: Path, rel_path: str) -> Dict[str, Any]:
        name = ws_dir.name
        description = ""
        pkg = ws_dir / "package.json"
        if pkg.is_file():
            data = _read_json(pkg)
            if data:
                name = data.get("name", name)
                description = data.get("description", "")
        else:
            cargo = ws_dir / "Cargo.toml"
            if cargo.is_file():
                text = _read_text(cargo)
                if text:
                    m = re.search(r'name\s*=\s*"([^"]+)"', text)
                    if m:
                        name = m.group(1)
                    m2 = re.search(r'description\s*=\s*"([^"]+)"', text)
                    if m2:
                        description = m2.group(1)
        return {"path": rel_path, "name": name, "description": description}

    # ── tech stack ───────────────────────────────────────────────────────

    def detect_tech_stack(self, workspaces: List[Dict[str, Any]]) -> None:
        """Populate self.tech_stack from config files."""
        # Root-level detection
        run_all_detectors(self.root, self.root, None, self._add_tech, self._warn)

        # Per-workspace detection
        for ws in workspaces:
            ws_path = self.root / ws["path"]
            ws_name = ws.get("name") or ws["path"]
            run_all_detectors(self.root, ws_path, ws_name, self._add_tech, self._warn)

    # ── linting & formatting ─────────────────────────────────────────────

    def detect_linting(self) -> Dict[str, Any]:
        tools: List[str] = []
        key_rules: Dict[str, str] = {}
        strict_mode = False

        # ESLint (flat config and legacy)
        eslint_files = (
            list(self.root.glob(".eslintrc*"))
            + list(self.root.glob("eslint.config.*"))
        )
        if eslint_files:
            tools.append("eslint")
            for ef in eslint_files:
                self._extract_eslint_rules(ef, key_rules)

        # Biome
        biome = self.root / "biome.json"
        biome_alt = self.root / "biome.jsonc"
        for bp in (biome, biome_alt):
            if bp.is_file():
                tools.append("biome")
                data = _read_json(bp)
                if data:
                    linter = data.get("linter", {})
                    if linter.get("enabled") is True:
                        strict_mode = True
                break

        # Prettier
        prettier_files = list(self.root.glob(".prettierrc*"))
        prettier_files += [self.root / "prettier.config.js", self.root / "prettier.config.mjs"]
        if any(p.is_file() for p in prettier_files):
            tools.append("prettier")

        # Rust
        if (self.root / "rustfmt.toml").is_file() or (self.root / ".rustfmt.toml").is_file():
            tools.append("rustfmt")
        if (self.root / "clippy.toml").is_file() or (self.root / ".clippy.toml").is_file():
            tools.append("clippy")

        # Go
        for name in (".golangci.yml", ".golangci.yaml", ".golangci.toml"):
            if (self.root / name).is_file():
                tools.append("golangci-lint")
                break

        # Python
        ruff_toml = self.root / "ruff.toml"
        pyproject = self.root / "pyproject.toml"
        if ruff_toml.is_file():
            tools.append("ruff")
        elif pyproject.is_file():
            text = _read_text(pyproject)
            if text and "[tool.ruff]" in text:
                tools.append("ruff")

        # Ruby
        if (self.root / ".rubocop.yml").is_file():
            tools.append("rubocop")

        # Editor config
        if (self.root / ".editorconfig").is_file():
            tools.append("editorconfig")

        # Deno
        if (self.root / "deno.json").is_file() or (self.root / "deno.jsonc").is_file():
            tools.append("deno")

        # Check tsconfig strict mode
        ts_cfg = self.root / "tsconfig.json"
        if ts_cfg.is_file():
            text = _read_text(ts_cfg)
            if text:
                stripped = re.sub(r"//.*?$", "", text, flags=re.MULTILINE)
                stripped = re.sub(r",(\s*[}\]])", r"\1", stripped)
                try:
                    data = json.loads(stripped)
                    co = data.get("compilerOptions", {})
                    if co.get("strict"):
                        strict_mode = True
                    for k in ("noAny", "noImplicitAny", "noImplicitReturns",
                              "noImplicitThis", "noUncheckedIndexedAccess",
                              "strictNullChecks", "strictFunctionTypes"):
                        if co.get(k) is True:
                            key_rules[k] = "enabled"
                except json.JSONDecodeError:
                    pass

        return {
            "tools": tools,
            "strict_mode": strict_mode,
            "key_rules": key_rules,
        }

    def _extract_eslint_rules(self, path: Path, key_rules: Dict[str, str]) -> None:
        """Extract rules set to 'error' from an ESLint config."""
        if path.suffix in (".json", ""):
            data = _read_json(path)
            if data and "rules" in data:
                for rule, val in data["rules"].items():
                    level = val if isinstance(val, str) else (val[0] if isinstance(val, list) else None)
                    if level == "error" or level == 2:
                        key_rules[rule] = "error"
                    elif level == "warn" or level == 1:
                        key_rules[rule] = "warn"
        elif path.suffix in (".js", ".cjs", ".mjs", ".ts"):
            # Best-effort regex extraction from JS config files
            text = _read_text(path)
            if text:
                # Match patterns like "rule-name": "error" or 'rule-name': 'error'
                for m in re.finditer(r"""['"]([a-zA-Z@/\-]+)['"]\s*:\s*['"]error['"]""", text):
                    key_rules[m.group(1)] = "error"
                for m in re.finditer(r"""['"]([a-zA-Z@/\-]+)['"]\s*:\s*['"]warn['"]""", text):
                    key_rules[m.group(1)] = "warn"

    # ── standards docs ───────────────────────────────────────────────────

    def detect_standards_docs(self) -> List[Dict[str, Any]]:
        docs: List[Dict[str, Any]] = []
        patterns = [
            "CLAUDE.md", "**/CLAUDE.md",
            "CONTRIBUTING.md", "CODE_OF_CONDUCT.md", "ARCHITECTURE.md",
            "**/*.standards.md", "**/*.conventions.md",
        ]
        seen: Set[str] = set()
        for pat in patterns:
            for p in self.root.glob(pat):
                if p.is_file() and not any(skip in p.parts for skip in SKIP_DIRS):
                    rel = str(p.relative_to(self.root))
                    if rel not in seen:
                        seen.add(rel)
                        docs.append({"path": rel, "size_bytes": p.stat().st_size})

        # Check specific dirs
        for dirname in ("standards", "docs", "ADR", "docs/adr"):
            d = self.root / dirname
            if d.is_dir():
                rel = str(d.relative_to(self.root))
                if rel not in seen:
                    # Report the directory itself
                    total_size = sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
                    seen.add(rel)
                    docs.append({"path": rel + "/", "size_bytes": total_size})

        return docs

    # ── CI/CD ────────────────────────────────────────────────────────────

    def detect_cicd(self) -> Dict[str, Any]:
        platform = None
        workflows: List[str] = []

        gh_dir = self.root / ".github" / "workflows"
        if gh_dir.is_dir():
            platform = "github-actions"
            for f in sorted(gh_dir.iterdir()):
                if f.is_file() and f.suffix in (".yml", ".yaml"):
                    workflows.append(str(f.relative_to(self.root)))

        if (self.root / "Jenkinsfile").is_file():
            platform = platform or "jenkins"
            workflows.append("Jenkinsfile")

        if (self.root / ".gitlab-ci.yml").is_file():
            platform = platform or "gitlab-ci"
            workflows.append(".gitlab-ci.yml")

        circleci = self.root / ".circleci"
        if circleci.is_dir():
            platform = platform or "circleci"
            for f in circleci.rglob("*.yml"):
                workflows.append(str(f.relative_to(self.root)))

        if (self.root / "bitbucket-pipelines.yml").is_file():
            platform = platform or "bitbucket-pipelines"
            workflows.append("bitbucket-pipelines.yml")

        if (self.root / ".travis.yml").is_file():
            platform = platform or "travis-ci"
            workflows.append(".travis.yml")

        return {
            "platform": platform,
            "workflows": workflows,
        }

    # ── file distribution ────────────────────────────────────────────────

    def detect_file_distribution(self) -> Tuple[Dict[str, Dict[str, Any]], int]:
        ext_counts: Dict[str, int] = {}
        total = 0

        for dirpath, dirnames, filenames in os.walk(self.root):
            # Prune skip dirs in-place
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for fname in filenames:
                total += 1
                _, ext = os.path.splitext(fname)
                ext = ext.lower() if ext else "(no extension)"
                ext_counts[ext] = ext_counts.get(ext, 0) + 1

        # Build sorted result with percentages, top 20
        sorted_exts = sorted(ext_counts.items(), key=lambda x: x[1], reverse=True)[:20]
        languages: Dict[str, Dict[str, Any]] = {}

        ext_to_lang = {
            ".ts": "TypeScript", ".tsx": "TypeScript", ".js": "JavaScript",
            ".jsx": "JavaScript", ".mjs": "JavaScript", ".cjs": "JavaScript",
            ".py": "Python", ".rs": "Rust", ".go": "Go", ".rb": "Ruby",
            ".java": "Java", ".kt": "Kotlin", ".cs": "C#", ".fs": "F#",
            ".swift": "Swift", ".dart": "Dart", ".ex": "Elixir",
            ".exs": "Elixir", ".php": "PHP", ".vue": "Vue",
            ".svelte": "Svelte", ".html": "HTML", ".css": "CSS",
            ".scss": "SCSS", ".sass": "Sass", ".less": "Less",
            ".json": "JSON", ".yaml": "YAML", ".yml": "YAML",
            ".toml": "TOML", ".md": "Markdown", ".sql": "SQL",
            ".sh": "Shell", ".bash": "Shell", ".zsh": "Shell",
            ".c": "C", ".cpp": "C++", ".h": "C/C++ Header",
            ".hpp": "C++ Header", ".lua": "Lua", ".zig": "Zig",
            ".astro": "Astro", ".graphql": "GraphQL", ".gql": "GraphQL",
            ".proto": "Protocol Buffers", ".r": "R",
        }

        for ext, count in sorted_exts:
            lang = ext_to_lang.get(ext, ext)
            if lang in languages:
                languages[lang]["files"] += count
            else:
                pct = round((count / total) * 100, 1) if total > 0 else 0
                languages[lang] = {"files": count, "percentage": pct}

        # Recalculate percentages for merged languages
        for lang_data in languages.values():
            lang_data["percentage"] = round((lang_data["files"] / total) * 100, 1) if total > 0 else 0

        return languages, total

    # ── git info ─────────────────────────────────────────────────────────

    def detect_git(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "current_branch": None,
            "default_branch": None,
            "recent_commits_30d": None,
            "contributors": None,
        }

        # Check if git is available
        if not (self.root / ".git").exists():
            self._warn("Not a git repository — git info skipped")
            return result

        # Current branch
        branch = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], self.root)
        result["current_branch"] = branch

        # Default branch — try remote, fall back to common names
        default = _run(
            ["git", "symbolic-ref", "refs/remotes/origin/HEAD", "--short"],
            self.root,
        )
        if default:
            result["default_branch"] = default.replace("origin/", "")
        else:
            # Fallback: check common names
            for candidate in ("main", "master", "develop"):
                check = _run(["git", "rev-parse", "--verify", candidate], self.root)
                if check:
                    result["default_branch"] = candidate
                    break

        # Recent commits (30 days)
        since = _run(
            ["git", "rev-list", "--count", "--since=30.days", "HEAD"],
            self.root,
        )
        if since and since.isdigit():
            result["recent_commits_30d"] = int(since)

        # Contributors
        contribs = _run(
            ["git", "log", "--format=%aN", "--since=365.days"],
            self.root,
        )
        if contribs:
            unique = set(contribs.strip().splitlines())
            result["contributors"] = len(unique)

        return result

    # ── main scan ────────────────────────────────────────────────────────

    def scan(self) -> Dict[str, Any]:
        """Run all detectors and return the final JSON-serializable dict."""
        # Project structure
        project = self.detect_project()

        # Tech stack
        self.detect_tech_stack(project.get("workspaces", []))

        # Deduplicate tech stack (same name+workspace)
        seen_tech: Set[Tuple[str, Optional[str]]] = set()
        deduped: List[Dict[str, Any]] = []
        for entry in self.tech_stack:
            key = (entry["name"], entry.get("workspace"))
            if key not in seen_tech:
                seen_tech.add(key)
                deduped.append(entry)
        self.tech_stack = deduped

        # File distribution
        languages, total_files = self.detect_file_distribution()

        # Linting
        linting = self.detect_linting()

        # Standards docs
        standards = self.detect_standards_docs()

        # CI/CD
        cicd = self.detect_cicd()

        # Git
        git_info = self.detect_git()

        return {
            "scanner_version": SCANNER_VERSION,
            "scanned_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "project": project,
            "tech_stack": self.tech_stack,
            "languages": languages,
            "total_files": total_files,
            "linting": linting,
            "standards_docs": standards,
            "ci_cd": cicd,
            "git": git_info,
            "warnings": self.warnings if self.warnings else [],
        }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scan a codebase and output structured JSON for code review.",
    )
    parser.add_argument(
        "--path", type=str, default=".",
        help="Path to the project root (default: current directory)",
    )
    parser.add_argument(
        "--pretty", action="store_true",
        help="Pretty-print the JSON output",
    )
    args = parser.parse_args()

    root = Path(args.path).resolve()
    if not root.is_dir():
        print(json.dumps({"error": f"Not a directory: {root}"}), file=sys.stderr)
        sys.exit(1)

    scanner = ProjectScanner(root)
    result = scanner.scan()

    indent = 2 if args.pretty else None
    print(json.dumps(result, indent=indent, ensure_ascii=False))


if __name__ == "__main__":
    main()
