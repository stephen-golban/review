#!/usr/bin/env python3
"""
detectors.py — Language and technology detection functions.

Standalone detector functions for identifying languages, frameworks,
and tools from project configuration files. Used by scanner.py.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable, Dict, Optional

# ── Tech-stack categorization tables ────────────────────────────────────────

FRAMEWORK_PKGS = {
    "react", "vue", "angular", "@angular/core", "svelte", "next", "nuxt",
    "remix", "astro", "solid-js", "qwik", "express", "fastify", "koa",
    "hono", "@nestjs/core", "nest", "@nestjs/common",
}

UI_LIBRARY_PKGS = {
    "@mui/material", "@chakra-ui/react", "antd", "@headlessui/react",
    "tailwindcss", "nativewind", "styled-components", "shadcn",
}
UI_LIBRARY_PREFIXES = ("@radix-ui/", "@emotion/")

STATE_MGMT_PKGS = {
    "zustand", "redux", "@reduxjs/toolkit", "recoil", "jotai", "valtio",
    "mobx", "pinia", "vuex", "xstate",
}

DATA_FETCHING_PKGS = {
    "@tanstack/react-query", "swr", "apollo", "@apollo/client", "urql",
    "@trpc/client", "@trpc/server", "trpc", "axios", "ky", "got",
}

TESTING_PKGS = {
    "jest", "vitest", "mocha", "cypress", "playwright",
    "@playwright/test", "pytest",
}
TESTING_PREFIXES = ("@testing-library/",)

BUILD_TOOL_PKGS = {
    "vite", "webpack", "esbuild", "turbo", "nx", "rollup", "parcel",
    "tsup", "unbuild",
}

ORM_DB_PKGS = {
    "prisma", "@prisma/client", "drizzle-orm", "typeorm", "sequelize",
    "knex", "mongoose",
}

LINTING_PKGS = {
    "eslint", "prettier", "biome", "@biomejs/biome", "stylelint",
    "oxlint",
}

CSS_STYLING_PKGS = {
    "tailwindcss", "sass", "less", "postcss", "styled-components",
    "nativewind",
}
CSS_STYLING_PREFIXES = ("@emotion/",)


def _categorize_pkg(name: str) -> Optional[str]:
    """Return category string for a known package, or None to skip."""
    if name in FRAMEWORK_PKGS:
        return "framework"
    if name in UI_LIBRARY_PKGS or any(name.startswith(p) for p in UI_LIBRARY_PREFIXES):
        return "ui-library"
    if name in STATE_MGMT_PKGS:
        return "state-management"
    if name in DATA_FETCHING_PKGS:
        return "data-fetching"
    if name in TESTING_PKGS or any(name.startswith(p) for p in TESTING_PREFIXES):
        return "testing"
    if name in BUILD_TOOL_PKGS:
        return "build-tool"
    if name in ORM_DB_PKGS:
        return "orm/database"
    if name in LINTING_PKGS:
        return "linting"
    if name in CSS_STYLING_PKGS or any(name.startswith(p) for p in CSS_STYLING_PREFIXES):
        return "css/styling"
    return None


# ── Utility helpers ─────────────────────────────────────────────────────────

def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    """Read a JSON file, returning None on any failure."""
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return None


def _read_text(path: Path) -> Optional[str]:
    """Read a text file, returning None on failure."""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None


# ── Detection functions ────────────────────────────────────────────────────

def detect_node(base: Path, workspace: Optional[str], add_tech: Callable, warn: Callable) -> None:
    pkg_path = base / "package.json"
    if not pkg_path.is_file():
        return
    data = _read_json(pkg_path)
    if not data:
        return
    all_deps: Dict[str, str] = {}
    for field in ("dependencies", "devDependencies", "peerDependencies"):
        all_deps.update(data.get(field, {}))
    for name, version in all_deps.items():
        cat = _categorize_pkg(name)
        if cat:
            clean_version = re.sub(r"^[\^~>=<]+", "", version).strip()
            add_tech(name, clean_version, cat, "package.json", workspace)


def detect_typescript(base: Path, workspace: Optional[str], add_tech: Callable, warn: Callable) -> None:
    ts_cfg = base / "tsconfig.json"
    if not ts_cfg.is_file():
        return
    # tsconfig may have comments — strip them before parsing
    text = _read_text(ts_cfg)
    if not text:
        return
    # Strip single-line comments (// ...) and trailing commas
    stripped = re.sub(r"//.*?$", "", text, flags=re.MULTILINE)
    stripped = re.sub(r",(\s*[}\]])", r"\1", stripped)
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        warn(f"Could not parse {ts_cfg}")
        return
    compiler = data.get("compilerOptions", {})
    strict = compiler.get("strict", False)
    # Try to find TS version from package.json
    pkg = base / "package.json"
    ts_version = None
    if pkg.is_file():
        pkg_data = _read_json(pkg)
        if pkg_data:
            for field in ("devDependencies", "dependencies"):
                v = pkg_data.get(field, {}).get("typescript")
                if v:
                    ts_version = re.sub(r"^[\^~>=<]+", "", v).strip()
                    break
    add_tech(
        "typescript", ts_version, "language",
        "tsconfig.json", workspace,
    )
    # Record strict-mode info as a separate entry for visibility
    if strict:
        add_tech(
            "typescript-strict-mode", None, "config",
            "tsconfig.json", workspace,
        )


def detect_rust(base: Path, workspace: Optional[str], add_tech: Callable, warn: Callable) -> None:
    cargo = base / "Cargo.toml"
    if not cargo.is_file():
        return
    text = _read_text(cargo)
    if not text:
        return
    edition = None
    m = re.search(r'edition\s*=\s*"(\d{4})"', text)
    if m:
        edition = m.group(1)
    add_tech("rust", edition, "language", "Cargo.toml", workspace)
    # Extract key deps from [dependencies] section
    in_deps = False
    for line in text.splitlines():
        stripped = line.strip()
        if re.match(r"\[dependencies\]", stripped) or re.match(r"\[dev-dependencies\]", stripped):
            in_deps = True
            continue
        if stripped.startswith("[") and in_deps:
            in_deps = False
            continue
        if in_deps:
            dm = re.match(r'(\S+)\s*=\s*(?:"([^"]+)"|\{[^}]*version\s*=\s*"([^"]+)")', stripped)
            if dm:
                dep_name = dm.group(1)
                dep_ver = dm.group(2) or dm.group(3)
                known_rust = {
                    "actix-web": "framework", "axum": "framework", "rocket": "framework",
                    "tokio": "runtime", "serde": "serialization", "diesel": "orm/database",
                    "sqlx": "orm/database", "sea-orm": "orm/database",
                }
                cat = known_rust.get(dep_name)
                if cat:
                    add_tech(dep_name, dep_ver, cat, "Cargo.toml", workspace)


def detect_go(base: Path, workspace: Optional[str], add_tech: Callable, warn: Callable) -> None:
    go_mod = base / "go.mod"
    if not go_mod.is_file():
        return
    text = _read_text(go_mod)
    if not text:
        return
    # Go version
    m = re.search(r"^go\s+(\S+)", text, re.MULTILINE)
    go_ver = m.group(1) if m else None
    add_tech("go", go_ver, "language", "go.mod", workspace)
    # Key modules
    known_go = {
        "github.com/gin-gonic/gin": ("gin", "framework"),
        "github.com/gofiber/fiber": ("fiber", "framework"),
        "github.com/labstack/echo": ("echo", "framework"),
        "gorm.io/gorm": ("gorm", "orm/database"),
        "github.com/stretchr/testify": ("testify", "testing"),
    }
    for mod_path, (name, cat) in known_go.items():
        if mod_path in text:
            # Extract version
            vm = re.search(re.escape(mod_path) + r"\s+(\S+)", text)
            ver = vm.group(1) if vm else None
            add_tech(name, ver, cat, "go.mod", workspace)


def detect_python(base: Path, workspace: Optional[str], add_tech: Callable, warn: Callable) -> None:
    pyproject = base / "pyproject.toml"
    requirements = base / "requirements.txt"
    pipfile = base / "Pipfile"

    known_py: Dict[str, str] = {
        "django": "framework", "flask": "framework", "fastapi": "framework",
        "starlette": "framework", "sqlalchemy": "orm/database",
        "pytest": "testing", "ruff": "linting", "black": "linting",
        "mypy": "linting", "pydantic": "validation",
        "celery": "task-queue", "alembic": "orm/database",
    }

    found_any = False

    if pyproject.is_file():
        found_any = True
        text = _read_text(pyproject)
        if text:
            # Check for python version requirement
            m = re.search(r'requires-python\s*=\s*"([^"]+)"', text)
            if m:
                add_tech("python", m.group(1), "language", "pyproject.toml", workspace)
            else:
                add_tech("python", None, "language", "pyproject.toml", workspace)
            # Extract deps
            for dep_name, cat in known_py.items():
                # Match various pyproject.toml dependency formats
                if re.search(r'(?:^|\s|"|,)' + re.escape(dep_name) + r'(?:\s|[>=<~!\[",\]]|$)', text, re.IGNORECASE):
                    ver_m = re.search(re.escape(dep_name) + r'[>=<~!]+\s*([\d.]+)', text, re.IGNORECASE)
                    ver = ver_m.group(1) if ver_m else None
                    add_tech(dep_name, ver, cat, "pyproject.toml", workspace)

    if requirements.is_file() and not found_any:
        found_any = True
        add_tech("python", None, "language", "requirements.txt", workspace)
        text = _read_text(requirements)
        if text:
            for dep_name, cat in known_py.items():
                pattern = re.compile(r"^" + re.escape(dep_name) + r"(?:[>=<~!]+(.+))?$", re.MULTILINE | re.IGNORECASE)
                m = pattern.search(text)
                if m:
                    ver = m.group(1).strip() if m.group(1) else None
                    add_tech(dep_name, ver, cat, "requirements.txt", workspace)

    if pipfile.is_file() and not found_any:
        add_tech("python", None, "language", "Pipfile", workspace)


def detect_ruby(base: Path, workspace: Optional[str], add_tech: Callable, warn: Callable) -> None:
    gemfile = base / "Gemfile"
    if not gemfile.is_file():
        return
    text = _read_text(gemfile)
    if not text:
        return
    add_tech("ruby", None, "language", "Gemfile", workspace)
    known_ruby = {
        "rails": "framework", "rspec": "testing", "rubocop": "linting",
        "sidekiq": "task-queue", "devise": "authentication",
        "activerecord": "orm/database",
    }
    for gem, cat in known_ruby.items():
        if re.search(r"gem\s+['\"]" + re.escape(gem) + r"['\"]", text):
            vm = re.search(r"gem\s+['\"]" + re.escape(gem) + r"['\"],\s*['\"]([^'\"]+)['\"]", text)
            ver = vm.group(1) if vm else None
            add_tech(gem, ver, cat, "Gemfile", workspace)


def detect_java(base: Path, workspace: Optional[str], add_tech: Callable, warn: Callable) -> None:
    pom = base / "pom.xml"
    gradle = base / "build.gradle"
    gradle_kts = base / "build.gradle.kts"

    if pom.is_file():
        add_tech("java", None, "language", "pom.xml", workspace)
        text = _read_text(pom)
        if text:
            if "spring-boot" in text:
                vm = re.search(r"<spring-boot.version>([^<]+)</spring-boot.version>", text)
                ver = vm.group(1) if vm else None
                add_tech("spring-boot", ver, "framework", "pom.xml", workspace)

    if gradle.is_file() or gradle_kts.is_file():
        which = gradle if gradle.is_file() else gradle_kts
        lang = "kotlin" if gradle_kts.is_file() else "java"
        add_tech(lang, None, "language", which.name, workspace)
        text = _read_text(which)
        if text and "spring" in text.lower():
            add_tech("spring-boot", None, "framework", which.name, workspace)


def detect_dotnet(base: Path, workspace: Optional[str], add_tech: Callable, warn: Callable) -> None:
    csproj_files = list(base.glob("*.csproj"))
    sln_files = list(base.glob("*.sln"))
    if csproj_files:
        add_tech(".NET", None, "platform", csproj_files[0].name, workspace)
    elif sln_files:
        add_tech(".NET", None, "platform", sln_files[0].name, workspace)


def detect_php(base: Path, workspace: Optional[str], add_tech: Callable, warn: Callable) -> None:
    composer = base / "composer.json"
    if not composer.is_file():
        return
    data = _read_json(composer)
    if not data:
        return
    add_tech("php", None, "language", "composer.json", workspace)
    all_deps: Dict[str, str] = {}
    all_deps.update(data.get("require", {}))
    all_deps.update(data.get("require-dev", {}))
    if "laravel/framework" in all_deps:
        add_tech("laravel", all_deps["laravel/framework"], "framework", "composer.json", workspace)


def detect_dart(base: Path, workspace: Optional[str], add_tech: Callable, warn: Callable) -> None:
    pubspec = base / "pubspec.yaml"
    if not pubspec.is_file():
        return
    text = _read_text(pubspec)
    if not text:
        return
    add_tech("dart", None, "language", "pubspec.yaml", workspace)
    if "flutter:" in text:
        add_tech("flutter", None, "framework", "pubspec.yaml", workspace)


def detect_swift(base: Path, workspace: Optional[str], add_tech: Callable, warn: Callable) -> None:
    pkg = base / "Package.swift"
    if not pkg.is_file():
        return
    add_tech("swift", None, "language", "Package.swift", workspace)


def detect_elixir(base: Path, workspace: Optional[str], add_tech: Callable, warn: Callable) -> None:
    mix = base / "mix.exs"
    if not mix.is_file():
        return
    text = _read_text(mix)
    if not text:
        return
    add_tech("elixir", None, "language", "mix.exs", workspace)
    if ":phoenix" in text:
        vm = re.search(r':phoenix,\s*"~>\s*([\d.]+)"', text)
        ver = vm.group(1) if vm else None
        add_tech("phoenix", ver, "framework", "mix.exs", workspace)


# ── Convenience runner ─────────────────────────────────────────────────────

def run_all_detectors(root: Path, base: Path, workspace: Optional[str], add_tech: Callable, warn: Callable) -> None:
    """Run all language/tech detectors on a directory."""
    detect_node(base, workspace, add_tech, warn)
    detect_typescript(base, workspace, add_tech, warn)
    detect_rust(base, workspace, add_tech, warn)
    detect_go(base, workspace, add_tech, warn)
    detect_python(base, workspace, add_tech, warn)
    detect_ruby(base, workspace, add_tech, warn)
    detect_java(base, workspace, add_tech, warn)
    detect_dotnet(base, workspace, add_tech, warn)
    detect_php(base, workspace, add_tech, warn)
    detect_dart(base, workspace, add_tech, warn)
    detect_swift(base, workspace, add_tech, warn)
    detect_elixir(base, workspace, add_tech, warn)
