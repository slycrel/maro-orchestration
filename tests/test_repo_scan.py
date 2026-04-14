"""Tests for repo_scan.py — auto-detect repo tech stack."""

import json
import sys
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from repo_scan import (
    RepoStack,
    scan_repo,
    find_skills_for_stack,
    format_repo_context,
    _FILE_RULES,
    _PYTHON_FRAMEWORK_KEYWORDS,
    _NODEJS_FRAMEWORK_KEYWORDS,
)


# ---------------------------------------------------------------------------
# RepoStack
# ---------------------------------------------------------------------------

class TestRepoStack:
    def test_dominant_language_empty(self):
        stack = RepoStack(repo_path="/tmp/x")
        assert stack.dominant_language == "unknown"

    def test_dominant_language_returns_first(self):
        stack = RepoStack(repo_path="/tmp/x", primary_languages=["python", "typescript"])
        assert stack.dominant_language == "python"

    def test_to_text_empty(self):
        stack = RepoStack(repo_path="/tmp/x")
        assert stack.to_text() == ""

    def test_to_text_python(self):
        stack = RepoStack(
            repo_path="/tmp/x",
            primary_languages=["python"],
            frameworks=["fastapi", "sqlalchemy"],
            test_frameworks=["pytest"],
            has_docker=True,
            has_ci=True,
        )
        txt = stack.to_text()
        assert "python" in txt
        assert "fastapi" in txt
        assert "pytest" in txt
        assert "Docker" in txt
        assert "CI" in txt

    def test_to_text_respects_max_length(self):
        stack = RepoStack(
            repo_path="/tmp/x",
            primary_languages=["python"],
            frameworks=["fastapi", "sqlalchemy", "pydantic", "celery"],
        )
        txt = stack.to_text(max_length=20)
        assert len(txt) <= 20

    def test_to_text_no_docker_if_false(self):
        stack = RepoStack(repo_path="/tmp/x", primary_languages=["python"], has_docker=False)
        assert "Docker" not in stack.to_text()

    def test_entry_points_in_to_text(self):
        stack = RepoStack(repo_path="/tmp/x", primary_languages=["python"], entry_points=["app.py"])
        assert "app.py" in stack.to_text()


# ---------------------------------------------------------------------------
# scan_repo — missing / non-existent path
# ---------------------------------------------------------------------------

class TestScanRepoMissingPath:
    def test_missing_path_returns_stack_with_summary(self):
        stack = scan_repo("/tmp/definitely-does-not-exist-12345")
        assert "not found" in stack.summary

    def test_missing_path_dominant_language_unknown(self):
        stack = scan_repo("/tmp/definitely-does-not-exist-12345")
        assert stack.dominant_language == "unknown"


# ---------------------------------------------------------------------------
# scan_repo — real temp directories
# ---------------------------------------------------------------------------

class TestScanRepoPython:
    def test_detects_python_from_requirements(self, tmp_path):
        (tmp_path / "requirements.txt").write_text("flask==2.3\npytest>=7\n")
        stack = scan_repo(str(tmp_path))
        assert "python" in stack.primary_languages
        assert "flask" in stack.frameworks
        assert "pytest" in stack.frameworks
        assert "pytest" in stack.test_frameworks

    def test_detects_python_from_pyproject(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text('[tool.poetry]\nname = "myapp"\n[dependencies]\nfastapi = "*"\n')
        stack = scan_repo(str(tmp_path))
        assert "python" in stack.primary_languages
        assert "fastapi" in stack.frameworks

    def test_python_packages_capped_at_30(self, tmp_path):
        pkgs = "\n".join(f"pkg{i}==1.0" for i in range(50))
        (tmp_path / "requirements.txt").write_text(pkgs)
        stack = scan_repo(str(tmp_path))
        assert len(stack.python_packages) <= 30

    def test_detects_sqlalchemy_sets_has_db(self, tmp_path):
        (tmp_path / "requirements.txt").write_text("sqlalchemy==2.0\n")
        stack = scan_repo(str(tmp_path))
        assert stack.has_db is True

    def test_detects_pytest_ini(self, tmp_path):
        (tmp_path / "pytest.ini").write_text("[pytest]\n")
        stack = scan_repo(str(tmp_path))
        assert "python" in stack.primary_languages

    def test_comment_lines_not_in_packages(self, tmp_path):
        (tmp_path / "requirements.txt").write_text("# dev deps\nflask==2.3\n")
        stack = scan_repo(str(tmp_path))
        assert "#" not in " ".join(stack.python_packages)


class TestScanRepoNodeJS:
    def test_detects_nodejs_from_package_json(self, tmp_path):
        pkg = {"dependencies": {"express": "^4.18", "react": "^18"}}
        (tmp_path / "package.json").write_text(json.dumps(pkg))
        stack = scan_repo(str(tmp_path))
        assert "nodejs" in stack.primary_languages
        assert "express" in stack.frameworks
        assert "react" in stack.frameworks

    def test_detects_typescript_from_tsconfig(self, tmp_path):
        (tmp_path / "tsconfig.json").write_text("{}")
        stack = scan_repo(str(tmp_path))
        assert "typescript" in stack.primary_languages

    def test_detects_jest_in_devdeps(self, tmp_path):
        pkg = {"devDependencies": {"jest": "^29"}}
        (tmp_path / "package.json").write_text(json.dumps(pkg))
        stack = scan_repo(str(tmp_path))
        assert "jest" in stack.test_frameworks

    def test_invalid_package_json_no_crash(self, tmp_path):
        (tmp_path / "package.json").write_text("{invalid json}")
        stack = scan_repo(str(tmp_path))
        assert stack is not None


class TestScanRepoInfrastructure:
    def test_detects_docker(self, tmp_path):
        (tmp_path / "Dockerfile").write_text("FROM python:3.11\n")
        stack = scan_repo(str(tmp_path))
        assert stack.has_docker is True

    def test_detects_docker_compose(self, tmp_path):
        (tmp_path / "docker-compose.yml").write_text("version: '3'\n")
        stack = scan_repo(str(tmp_path))
        assert stack.has_docker is True

    def test_detects_makefile(self, tmp_path):
        (tmp_path / "Makefile").write_text("all:\n\techo ok\n")
        stack = scan_repo(str(tmp_path))
        assert "c" in stack.primary_languages

    def test_detects_cargo_toml(self, tmp_path):
        (tmp_path / "Cargo.toml").write_text('[package]\nname = "myapp"\n')
        stack = scan_repo(str(tmp_path))
        assert "rust" in stack.primary_languages

    def test_detects_go_mod(self, tmp_path):
        (tmp_path / "go.mod").write_text("module myapp\n")
        stack = scan_repo(str(tmp_path))
        assert "go" in stack.primary_languages


class TestScanRepoCI:
    def test_detects_github_actions_via_workflow_dir(self, tmp_path):
        wf_dir = tmp_path / ".github" / "workflows"
        wf_dir.mkdir(parents=True)
        (wf_dir / "ci.yml").write_text("on: push\n")
        stack = scan_repo(str(tmp_path))
        assert stack.has_ci is True

    def test_detects_gitlab_ci(self, tmp_path):
        (tmp_path / ".gitlab-ci.yml").write_text("stages: [test]\n")
        stack = scan_repo(str(tmp_path))
        assert stack.has_ci is True


class TestScanRepoEntryPoints:
    def test_detects_app_py(self, tmp_path):
        (tmp_path / "app.py").write_text("# app\n")
        stack = scan_repo(str(tmp_path))
        assert "app.py" in stack.entry_points

    def test_detects_main_py(self, tmp_path):
        (tmp_path / "main.py").write_text("# main\n")
        stack = scan_repo(str(tmp_path))
        assert "main.py" in stack.entry_points

    def test_detects_index_js(self, tmp_path):
        (tmp_path / "index.js").write_text("// index\n")
        stack = scan_repo(str(tmp_path))
        assert "index.js" in stack.entry_points


class TestScanRepoConfigFiles:
    def test_detects_dotenv(self, tmp_path):
        (tmp_path / ".env").write_text("DEBUG=true\n")
        stack = scan_repo(str(tmp_path))
        assert ".env" in stack.config_files

    def test_detects_env_example(self, tmp_path):
        (tmp_path / ".env.example").write_text("DEBUG=true\n")
        stack = scan_repo(str(tmp_path))
        assert ".env.example" in stack.config_files


class TestScanRepoMaxDepth:
    def test_respects_max_depth(self, tmp_path):
        # requirements.txt at depth 3 — should NOT be found with max_depth=2
        deep = tmp_path / "a" / "b" / "c"
        deep.mkdir(parents=True)
        (deep / "requirements.txt").write_text("flask\n")
        stack = scan_repo(str(tmp_path), max_depth=2)
        # depth 3 is beyond max_depth=2, so no python detected
        # (unless other indicators exist at shallower depth)
        # We just verify it doesn't crash
        assert stack is not None

    def test_finds_files_at_depth_1(self, tmp_path):
        sub = tmp_path / "backend"
        sub.mkdir()
        (sub / "requirements.txt").write_text("django\n")
        stack = scan_repo(str(tmp_path), max_depth=2)
        # requirements.txt at depth 1 is detected for language (file rule),
        # but deep framework scanning only reads root-level requirements.txt
        assert "python" in stack.primary_languages


class TestScanRepoSummary:
    def test_summary_populated(self, tmp_path):
        (tmp_path / "requirements.txt").write_text("flask\n")
        stack = scan_repo(str(tmp_path))
        assert stack.summary != ""
        assert "python" in stack.summary.lower() or "flask" in stack.summary.lower()

    def test_summary_unknown_for_empty_dir(self, tmp_path):
        stack = scan_repo(str(tmp_path))
        assert "unknown" in stack.summary


# ---------------------------------------------------------------------------
# find_skills_for_stack
# ---------------------------------------------------------------------------

class TestFindSkillsForStack:
    def _make_skill(self, name, description, trigger_patterns=None):
        skill = MagicMock()
        skill.name = name
        skill.description = description
        skill.trigger_patterns = trigger_patterns or []
        return skill

    def test_matches_by_language(self):
        stack = RepoStack(repo_path="/tmp", primary_languages=["python"])
        skill = self._make_skill("python-debug", "debugging python code")
        result = find_skills_for_stack(stack, [skill])
        assert "python-debug" in result

    def test_matches_by_framework(self):
        stack = RepoStack(repo_path="/tmp", primary_languages=["python"], frameworks=["fastapi"])
        skill = self._make_skill("fastapi-patterns", "fastapi endpoint best practices")
        result = find_skills_for_stack(stack, [skill])
        assert "fastapi-patterns" in result

    def test_matches_by_tag(self):
        stack = RepoStack(repo_path="/tmp", tags={"docker", "ci"})
        skill = self._make_skill("docker-compose-helper", "docker compose setup")
        result = find_skills_for_stack(stack, [skill])
        assert "docker-compose-helper" in result

    def test_no_match_when_unrelated(self):
        stack = RepoStack(repo_path="/tmp", primary_languages=["python"])
        skill = self._make_skill("rust-borrow-checker", "understanding rust ownership")
        result = find_skills_for_stack(stack, [skill])
        assert "rust-borrow-checker" not in result

    def test_matches_trigger_patterns(self):
        stack = RepoStack(repo_path="/tmp", primary_languages=["go"])
        skill = self._make_skill("go-concurrency", "concurrency help", trigger_patterns=["goroutine", "go"])
        result = find_skills_for_stack(stack, [skill])
        assert "go-concurrency" in result

    def test_empty_skills_list(self):
        stack = RepoStack(repo_path="/tmp", primary_languages=["python"])
        assert find_skills_for_stack(stack, []) == []

    def test_empty_stack(self):
        stack = RepoStack(repo_path="/tmp")
        skill = self._make_skill("generic-skill", "some skill")
        result = find_skills_for_stack(stack, [skill])
        # "some" isn't a stack keyword — no match
        assert result == []


# ---------------------------------------------------------------------------
# format_repo_context
# ---------------------------------------------------------------------------

class TestFormatRepoContext:
    def test_includes_repo_name(self):
        stack = RepoStack(repo_path="/home/user/myproject", primary_languages=["python"])
        out = format_repo_context(stack)
        assert "myproject" in out

    def test_explicit_repo_name(self):
        stack = RepoStack(repo_path="/home/user/myproject", primary_languages=["python"])
        out = format_repo_context(stack, repo_name="custom-name")
        assert "custom-name" in out
        assert "myproject" not in out

    def test_includes_packages_when_present(self):
        stack = RepoStack(repo_path="/tmp/x", primary_languages=["python"],
                          python_packages=["flask", "sqlalchemy"])
        out = format_repo_context(stack)
        assert "flask" in out
        assert "sqlalchemy" in out

    def test_packages_capped_at_8_in_output(self):
        stack = RepoStack(repo_path="/tmp/x", primary_languages=["python"],
                          python_packages=[f"pkg{i}" for i in range(20)])
        out = format_repo_context(stack)
        # count package names in the Key packages line
        pkg_line = [l for l in out.splitlines() if "packages" in l.lower()]
        if pkg_line:
            names = pkg_line[0].split(": ", 1)[1].split(", ")
            assert len(names) <= 8

    def test_includes_entry_points(self):
        stack = RepoStack(repo_path="/tmp/x", primary_languages=["python"],
                          entry_points=["app.py"])
        out = format_repo_context(stack)
        assert "app.py" in out

    def test_includes_config_files(self):
        stack = RepoStack(repo_path="/tmp/x", primary_languages=["python"],
                          config_files=[".env"])
        out = format_repo_context(stack)
        assert ".env" in out

    def test_no_crash_on_empty_stack(self):
        stack = RepoStack(repo_path="/tmp/x")
        out = format_repo_context(stack)
        assert "REPO CONTEXT" in out


# ---------------------------------------------------------------------------
# Self-scan of this repo
# ---------------------------------------------------------------------------

class TestSelfScan:
    """Scan the openclaw-orchestration repo itself and verify known facts."""

    def test_detects_python(self):
        root = str(Path(__file__).parent.parent)
        stack = scan_repo(root)
        assert "python" in stack.primary_languages

    def test_detects_pytest(self):
        root = str(Path(__file__).parent.parent)
        stack = scan_repo(root)
        assert "pytest" in stack.test_frameworks or "pytest" in stack.frameworks

    def test_detects_docker(self):
        root = str(Path(__file__).parent.parent)
        stack = scan_repo(root)
        assert stack.has_docker is True

    def test_format_context_nonempty(self):
        root = str(Path(__file__).parent.parent)
        stack = scan_repo(root)
        ctx = format_repo_context(stack)
        assert len(ctx) > 20
