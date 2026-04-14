"""Auto-detect repo tech stack and surface relevant skills + summary.

Implements the @ihtesham2005 steal: project scan → tech-stack detection →
skill suggestions + compact CLAUDE.md-style agent summary.

Usage:
    from repo_scan import scan_repo, format_repo_context
    stack = scan_repo("/path/to/repo")
    context = format_repo_context(stack)  # inject into agent context
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tech-stack detection rules
# ---------------------------------------------------------------------------

# (file_name_or_pattern, language/framework, tags)
_FILE_RULES: List[tuple] = [
    # Python
    ("requirements.txt",        "python",       ["python", "pip"]),
    ("requirements-dev.txt",    "python",       ["python", "pip", "dev"]),
    ("pyproject.toml",          "python",       ["python", "pyproject"]),
    ("setup.py",                "python",       ["python", "setuptools"]),
    ("setup.cfg",               "python",       ["python", "setuptools"]),
    ("Pipfile",                 "python",       ["python", "pipenv"]),
    ("tox.ini",                 "python",       ["python", "testing"]),
    ("pytest.ini",              "python",       ["python", "pytest"]),
    (".flake8",                 "python",       ["python", "linting"]),
    ("mypy.ini",                "python",       ["python", "types"]),
    # JavaScript / TypeScript
    ("package.json",            "nodejs",       ["javascript", "nodejs", "npm"]),
    ("yarn.lock",               "nodejs",       ["javascript", "yarn"]),
    ("pnpm-lock.yaml",          "nodejs",       ["javascript", "pnpm"]),
    ("tsconfig.json",           "typescript",   ["typescript"]),
    ("next.config.js",          "nextjs",       ["nextjs", "react"]),
    ("next.config.ts",          "nextjs",       ["nextjs", "react", "typescript"]),
    ("vite.config.ts",          "vite",         ["vite", "frontend"]),
    ("vite.config.js",          "vite",         ["vite", "frontend"]),
    ("angular.json",            "angular",      ["angular", "frontend"]),
    ("vue.config.js",           "vue",          ["vue", "frontend"]),
    # Rust
    ("Cargo.toml",              "rust",         ["rust", "cargo"]),
    # Go
    ("go.mod",                  "go",           ["go", "golang"]),
    # Java / JVM
    ("pom.xml",                 "java",         ["java", "maven"]),
    ("build.gradle",            "java",         ["java", "gradle"]),
    ("build.gradle.kts",        "kotlin",       ["kotlin", "gradle"]),
    # Ruby
    ("Gemfile",                 "ruby",         ["ruby", "bundler"]),
    ("Rakefile",                "ruby",         ["ruby", "rake"]),
    # PHP
    ("composer.json",           "php",          ["php", "composer"]),
    # C/C++
    ("CMakeLists.txt",          "cpp",          ["cpp", "cmake"]),
    ("Makefile",                "c",            ["c", "make"]),
    # Infrastructure
    ("Dockerfile",              "docker",       ["docker", "container"]),
    ("docker-compose.yml",      "docker",       ["docker", "compose"]),
    ("docker-compose.yaml",     "docker",       ["docker", "compose"]),
    (".github/workflows",       "ci_github",    ["ci", "github_actions"]),
    ("Jenkinsfile",             "ci_jenkins",   ["ci", "jenkins"]),
    (".gitlab-ci.yml",          "ci_gitlab",    ["ci", "gitlab"]),
    ("terraform.tf",            "terraform",    ["infra", "terraform"]),
    ("*.tf",                    "terraform",    ["infra", "terraform"]),
    # Database
    ("alembic.ini",             "sqlalchemy",   ["python", "sqlalchemy", "migration"]),
    ("migrations",              "db_migration", ["database", "migration"]),
    # Testing
    ("jest.config.js",          "jest",         ["testing", "jest"]),
    ("jest.config.ts",          "jest",         ["testing", "jest", "typescript"]),
    ("vitest.config.ts",        "vitest",       ["testing", "vitest"]),
    # Config
    (".env",                    "dotenv",       ["config", "env"]),
    (".env.example",            "dotenv",       ["config", "env"]),
    ("README.md",               "docs",         ["docs"]),
    ("CLAUDE.md",               "claude_code",  ["claude"]),
]

# Framework detection within Python (check requirements.txt / pyproject.toml content)
_PYTHON_FRAMEWORK_KEYWORDS: Dict[str, List[str]] = {
    "flask":        ["flask", "Flask"],
    "django":       ["django", "Django"],
    "fastapi":      ["fastapi", "FastAPI"],
    "sqlalchemy":   ["sqlalchemy", "SQLAlchemy"],
    "pydantic":     ["pydantic", "Pydantic"],
    "celery":       ["celery", "Celery"],
    "pytest":       ["pytest"],
    "anthropic":    ["anthropic"],
    "openai":       ["openai"],
}

_NODEJS_FRAMEWORK_KEYWORDS: Dict[str, List[str]] = {
    "express":  ["express"],
    "react":    ["react"],
    "vue":      ["vue"],
    "angular":  ["angular"],
    "fastify":  ["fastify"],
    "prisma":   ["prisma"],
}


@dataclass
class RepoStack:
    """Result of scanning a repository for its tech stack."""
    repo_path: str
    primary_languages: List[str] = field(default_factory=list)
    frameworks: List[str] = field(default_factory=list)
    tags: Set[str] = field(default_factory=set)
    file_indicators: List[str] = field(default_factory=list)
    test_frameworks: List[str] = field(default_factory=list)
    has_docker: bool = False
    has_ci: bool = False
    has_db: bool = False
    python_packages: List[str] = field(default_factory=list)   # from requirements.txt
    entry_points: List[str] = field(default_factory=list)      # main.py, app.py, etc.
    config_files: List[str] = field(default_factory=list)      # .env, config.yml, etc.
    summary: str = ""  # auto-generated compact description

    @property
    def dominant_language(self) -> str:
        return self.primary_languages[0] if self.primary_languages else "unknown"

    def to_text(self, max_length: int = 400) -> str:
        """Compact text representation for agent injection."""
        parts = []
        if self.primary_languages:
            parts.append(f"Language: {', '.join(self.primary_languages[:3])}")
        if self.frameworks:
            parts.append(f"Frameworks: {', '.join(self.frameworks[:5])}")
        if self.test_frameworks:
            parts.append(f"Testing: {', '.join(self.test_frameworks)}")
        if self.has_docker:
            parts.append("Has: Docker")
        if self.has_ci:
            parts.append("Has: CI/CD")
        if self.has_db:
            parts.append("Has: DB migrations")
        if self.entry_points:
            parts.append(f"Entry points: {', '.join(self.entry_points[:3])}")
        return " | ".join(parts)[:max_length]


# ---------------------------------------------------------------------------
# Scanner
# ---------------------------------------------------------------------------

def scan_repo(repo_path: str, *, max_depth: int = 2) -> RepoStack:
    """Scan a repository directory and detect its tech stack.

    Args:
        repo_path:   Path to the repository root.
        max_depth:   How many directory levels to scan (default: 2).

    Returns:
        RepoStack with detected languages, frameworks, and tags.
    """
    root = Path(repo_path).expanduser().resolve()
    if not root.exists():
        return RepoStack(repo_path=str(root), summary=f"(not found: {root})")

    stack = RepoStack(repo_path=str(root))
    detected_languages: Set[str] = set()
    detected_frameworks: Set[str] = set()
    detected_test_frameworks: Set[str] = set()
    all_tags: Set[str] = set()

    # Collect files up to max_depth
    def _iter_files(path: Path, depth: int = 0):
        if depth > max_depth:
            return
        try:
            for entry in path.iterdir():
                if entry.name.startswith(".") and entry.name not in (
                    ".github", ".env", ".env.example", ".flake8",
                    ".gitlab-ci.yml", ".gitlab-ci.yaml",
                ):
                    continue
                if entry.is_file():
                    yield entry
                elif entry.is_dir() and depth < max_depth:
                    # Don't recurse into large/binary dirs
                    if entry.name not in ("node_modules", "__pycache__", ".git", "venv",
                                          ".venv", "dist", "build", "target", ".tox"):
                        yield from _iter_files(entry, depth + 1)
        except (PermissionError, OSError):
            pass

    all_files = list(_iter_files(root))
    file_names = {f.name for f in all_files}
    file_paths_rel = {str(f.relative_to(root)) for f in all_files}

    # Apply file rules
    for file_pattern, lang, tags in _FILE_RULES:
        if "*" in file_pattern:
            # Glob pattern
            suffix = file_pattern.lstrip("*")
            matches = [f for f in file_names if f.endswith(suffix)]
        else:
            # Exact match (also check relative paths for subdirs like .github/workflows)
            matches = []
            if file_pattern in file_names:
                matches.append(file_pattern)
            if any(file_pattern in p for p in file_paths_rel):
                matches.append(file_pattern)

        if matches:
            if file_pattern not in stack.file_indicators:
                stack.file_indicators.append(file_pattern)
            detected_languages.add(lang)
            all_tags.update(tags)

            if "testing" in tags:
                detected_test_frameworks.add(lang)
            if "docker" in tags:
                stack.has_docker = True
            if "ci" in tags:
                stack.has_ci = True
            if "migration" in tags:
                stack.has_db = True

    # Deep scan Python requirements
    for req_file in ("requirements.txt", "requirements-dev.txt"):
        req_path = root / req_file
        if req_path.exists():
            try:
                content = req_path.read_text(encoding="utf-8", errors="ignore").lower()
                for fw, keywords in _PYTHON_FRAMEWORK_KEYWORDS.items():
                    if any(kw.lower() in content for kw in keywords):
                        detected_frameworks.add(fw)
                        if fw == "pytest":
                            detected_test_frameworks.add(fw)
                        if fw in ("sqlalchemy",):
                            stack.has_db = True
                # Collect package names
                packages = []
                for line in content.splitlines():
                    line = line.strip()
                    if line and not line.startswith("#"):
                        pkg = line.split("==")[0].split(">=")[0].split("<=")[0].strip()
                        if pkg:
                            packages.append(pkg)
                stack.python_packages = packages[:30]  # cap at 30
            except OSError:
                pass

    # Deep scan pyproject.toml
    pyproject = root / "pyproject.toml"
    if pyproject.exists():
        try:
            content = pyproject.read_text(encoding="utf-8", errors="ignore").lower()
            for fw, keywords in _PYTHON_FRAMEWORK_KEYWORDS.items():
                if any(kw.lower() in content for kw in keywords):
                    detected_frameworks.add(fw)
        except OSError:
            pass

    # Deep scan package.json for Node frameworks
    package_json = root / "package.json"
    if package_json.exists():
        try:
            data = json.loads(package_json.read_text(encoding="utf-8", errors="ignore"))
            all_deps = {}
            all_deps.update(data.get("dependencies", {}))
            all_deps.update(data.get("devDependencies", {}))
            dep_str = " ".join(all_deps.keys()).lower()
            for fw, keywords in _NODEJS_FRAMEWORK_KEYWORDS.items():
                if any(kw.lower() in dep_str for kw in keywords):
                    detected_frameworks.add(fw)
            if "jest" in dep_str or "@jest" in dep_str:
                detected_test_frameworks.add("jest")
            if "vitest" in dep_str:
                detected_test_frameworks.add("vitest")
        except (json.JSONDecodeError, OSError):
            pass

    # Detect entry points (common main files)
    for entry_name in ("main.py", "app.py", "server.py", "manage.py", "index.js", "index.ts",
                        "index.html", "main.go", "main.rs", "main.ts"):
        if entry_name in file_names:
            stack.entry_points.append(entry_name)

    # Config files
    for cfg_name in (".env", ".env.example", "config.yml", "config.yaml", "settings.py",
                     "config.json", "appsettings.json"):
        if cfg_name in file_names:
            stack.config_files.append(cfg_name)

    # Consolidate — language priority order
    lang_priority = ["python", "typescript", "nodejs", "rust", "go", "java", "kotlin",
                      "ruby", "php", "cpp"]
    stack.primary_languages = [l for l in lang_priority if l in detected_languages]
    remaining = [l for l in detected_languages if l not in lang_priority]
    stack.primary_languages.extend(remaining)

    # Frameworks (remove language names from frameworks list)
    stack.frameworks = [f for f in sorted(detected_frameworks)
                        if f not in ("python", "nodejs", "typescript")]
    stack.test_frameworks = sorted(detected_test_frameworks)
    stack.tags = all_tags

    # Build compact summary
    summary_parts = []
    if stack.primary_languages:
        summary_parts.append(stack.primary_languages[0])
    if stack.frameworks:
        summary_parts.extend(stack.frameworks[:3])
    extras = []
    if stack.has_docker:
        extras.append("Docker")
    if stack.has_ci:
        extras.append("CI/CD")
    if stack.has_db:
        extras.append("DB")
    if extras:
        summary_parts.append("+".join(extras))
    stack.summary = " / ".join(summary_parts) if summary_parts else "unknown stack"

    return stack


# ---------------------------------------------------------------------------
# Skill matching
# ---------------------------------------------------------------------------

def find_skills_for_stack(stack: RepoStack, skills) -> List[str]:
    """Return skill names that are relevant to the detected stack.

    Matches by checking skill tags and trigger_patterns against stack tags and frameworks.
    """
    relevant: List[str] = []
    stack_keywords = (
        set(stack.primary_languages) |
        set(stack.frameworks) |
        {t.lower() for t in stack.tags}
    )

    for skill in skills:
        skill_text = (
            skill.name.lower() + " " +
            skill.description.lower() + " " +
            " ".join(t.lower() for t in getattr(skill, "trigger_patterns", []))
        )
        if any(kw in skill_text for kw in stack_keywords):
            relevant.append(skill.name)

    return relevant


# ---------------------------------------------------------------------------
# Agent context injection
# ---------------------------------------------------------------------------

def format_repo_context(stack: RepoStack, *, repo_name: str = "") -> str:
    """Format repo stack as a compact injection block for agent context.

    Returns a short text block suitable for injection into decompose context.
    """
    if not repo_name:
        repo_name = Path(stack.repo_path).name

    lines = [f"REPO CONTEXT: {repo_name}"]
    if stack.primary_languages:
        lines.append(f"Stack: {stack.to_text()}")
    if stack.python_packages:
        lines.append(f"Key packages: {', '.join(stack.python_packages[:8])}")
    if stack.entry_points:
        lines.append(f"Entry: {', '.join(stack.entry_points)}")
    if stack.config_files:
        lines.append(f"Config: {', '.join(stack.config_files[:4])}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        prog="poe-repo-scan",
        description="Detect tech stack and surface relevant skills",
    )
    parser.add_argument("path", nargs="?", default=".", help="Repo path to scan (default: .)")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--context", action="store_true", help="Output agent context block")
    parser.add_argument("--skills", action="store_true", help="Show matching skills from library")
    args = parser.parse_args()

    stack = scan_repo(args.path)

    if args.json:
        import dataclasses
        d = dataclasses.asdict(stack)
        d["tags"] = sorted(d["tags"])
        print(json.dumps(d, indent=2))
        return

    if args.context:
        print(format_repo_context(stack))
        return

    print(f"Repo: {stack.repo_path}")
    print(f"Stack: {stack.summary}")
    print(f"Languages: {', '.join(stack.primary_languages) or '(none detected)'}")
    if stack.frameworks:
        print(f"Frameworks: {', '.join(stack.frameworks)}")
    if stack.test_frameworks:
        print(f"Testing: {', '.join(stack.test_frameworks)}")
    if stack.entry_points:
        print(f"Entry points: {', '.join(stack.entry_points)}")
    if stack.has_docker:
        print("Docker: yes")
    if stack.has_ci:
        print("CI/CD: yes")
    if stack.python_packages:
        print(f"Python packages ({len(stack.python_packages)}): {', '.join(stack.python_packages[:10])}")

    if args.skills:
        try:
            from skills import load_skills
            skills = load_skills()
            matches = find_skills_for_stack(stack, skills)
            if matches:
                print(f"\nMatching skills: {', '.join(matches[:10])}")
            else:
                print("\nNo matching skills found in library.")
        except Exception as e:
            print(f"\n(skill matching unavailable: {e})")


if __name__ == "__main__":
    main()
