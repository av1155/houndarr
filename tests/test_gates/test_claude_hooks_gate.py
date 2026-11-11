"""Consolidated invariant: the Claude Code hooks act on the input Claude Code sends.

Claude Code passes hook input as JSON on stdin (``tool_input.file_path``,
``tool_input.command``, ``last_assistant_message``) and sets no file-path
environment variable.  The edit hooks in ``.claude/settings.json`` once looped
over an unset ``$CLAUDE_FILE_PATHS`` and silently did nothing (#868), and
nothing in the suite ran them.  These tests run each hook command under bash
with that JSON, inside a throwaway git repo whose ``.venv`` links to the
environment running pytest, and check the hook acceptance criteria of #867 and
#868.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

_SETTINGS = Path(__file__).resolve().parents[2] / ".claude" / "settings.json"

_MISSING_TOOLS = [tool for tool in ("bash", "git", "jq") if shutil.which(tool) is None]

# In CI a missing tool fails the hook tests instead of skipping them.
pytestmark = pytest.mark.skipif(
    bool(_MISSING_TOOLS) and not os.environ.get("CI"),
    reason=f"the hook commands need {', '.join(_MISSING_TOOLS)}",
)

# Inherited GIT_* variables (set when pytest runs under a git hook) would point
# every git call at the outer repo; CLAUDE_PROJECT_DIR is set per test.
_ENV = {
    key: value
    for key, value in os.environ.items()
    if not key.startswith("GIT_") and key != "CLAUDE_PROJECT_DIR"
}
_GIT_ENV = {
    **_ENV,
    "GIT_AUTHOR_NAME": "test",
    "GIT_AUTHOR_EMAIL": "test@example.invalid",
    "GIT_COMMITTER_NAME": "test",
    "GIT_COMMITTER_EMAIL": "test@example.invalid",
}


def _hook(event: str, marker: str) -> str:
    """Return the one ``event`` hook command that contains ``marker``."""
    groups = json.loads(_SETTINGS.read_text())["hooks"][event]
    commands = [h["command"] for g in groups for h in g["hooks"] if marker in h["command"]]
    assert len(commands) == 1, f"expected one {event} hook containing {marker!r}"
    return commands[0]


_FORMAT_HOOK = _hook("PostToolUse", "ruff format")
_MYPY_HOOK = _hook("PostToolUse", "mypy")
_PUSH_GUARD_HOOK = _hook("PreToolUse", "Direct push to main")
_MAIN_GUARD_HOOK = _hook("PreToolUse", "Cannot edit repo files on main")
_COMMIT_HOOK = _hook("PreToolUse", "ruff format --check")
_STOP_HOOK = _hook("Stop", "last_assistant_message")

_UNFORMATTED_MD = "# Notes\n\n```python\nx=1\n```\n"

# Claude Code runs a hook command through bash or the user's $SHELL, and sh is dash on Linux.
_SHELLS = [shell for shell in ("bash", "sh", "zsh") if shutil.which(shell)]


def _run(
    command: str, payload: dict[str, Any], cwd: Path, shell: str = "bash", **env: str
) -> subprocess.CompletedProcess[str]:
    """Run a hook command the way Claude Code does: a shell, JSON on stdin, Claude's directory."""
    return subprocess.run(
        [shell, "-c", command],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        cwd=cwd,
        env={**_ENV, **env},
        timeout=120,
        check=False,
    )


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=cwd, env=_GIT_ENV, capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def _edit(path: Path) -> dict[str, Any]:
    return {"tool_name": "Write", "tool_input": {"file_path": str(path), "content": ""}}


def _bash(command: str) -> dict[str, Any]:
    return {"tool_name": "Bash", "tool_input": {"command": command}}


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """A git repo on a feature branch whose ``.venv`` links to this test run's environment."""
    root = tmp_path.resolve() / "repo"
    (root / "sub").mkdir(parents=True)
    _git(root, "init", "-q", "-b", "feat")
    (root / ".venv").symlink_to(sys.prefix, target_is_directory=True)
    (root / ".gitignore").write_text(".venv\nignored/\n")
    return root


@pytest.fixture(params=_SHELLS)
def shell(request: pytest.FixtureRequest) -> str:
    return str(request.param)


@pytest.fixture()
def main_repo(repo: Path) -> Path:
    """The repo with one commit on main, so a worktree can branch from it."""
    _git(repo, "symbolic-ref", "HEAD", "refs/heads/main")
    commit = _git(repo, "commit-tree", _git(repo, "write-tree"), "-m", "init")
    _git(repo, "update-ref", "HEAD", commit)
    return repo


@pytest.mark.parametrize("cwd", ["", "sub"])
def test_format_hook_formats_python_and_keeps_unused_imports(repo: Path, cwd: str) -> None:
    """Lint fixes and formatting apply, but an import added before its first use survives."""
    target = repo / "probe.py"
    target.write_text('import os\nx=f"a"\n')

    assert _run(_FORMAT_HOOK, _edit(target), repo / cwd).returncode == 0
    text = target.read_text()
    assert "import os" in text
    assert 'x = "a"' in text


def test_format_hook_keeps_an_exception_binding_used_later(repo: Path) -> None:
    """An ``except ... as e:`` written before the code that uses ``e`` survives too."""
    target = repo / "probe.py"
    target.write_text("try:\n    pass\nexcept ValueError as e:\n    pass\n")

    assert _run(_FORMAT_HOOK, _edit(target), repo).returncode == 0
    assert "except ValueError as e:" in target.read_text()


def test_format_hook_formats_python_blocks_in_markdown(repo: Path) -> None:
    target = repo / "notes.md"
    target.write_text(_UNFORMATTED_MD)

    assert _run(_FORMAT_HOOK, _edit(target), repo).returncode == 0
    assert target.read_text() == "# Notes\n\n```python\nx = 1\n```\n"


def test_format_hook_leaves_ignored_and_outside_files_alone(repo: Path, tmp_path: Path) -> None:
    """ruff's --force-exclude still formats a gitignored file passed by path."""
    (repo / "ignored").mkdir()
    (repo / "_build").mkdir()
    untouched = {
        repo / "ignored" / "probe.py": "x=1\n",
        repo / "ignored" / "notes.md": _UNFORMATTED_MD,
        repo / "config.yml": "a:    1\n",
        repo / "_build" / "probe.py": "x=1\n",
        tmp_path.resolve() / "outside.py": "x=1\n",
    }
    for path, text in untouched.items():
        path.write_text(text)

    for path, text in untouched.items():
        assert _run(_FORMAT_HOOK, _edit(path), repo).returncode == 0
        assert path.read_text() == text, path


def test_mypy_hook_reports_type_errors_to_claude(repo: Path) -> None:
    """A PostToolUse hook's plain-text stderr only reaches Claude when it exits 2."""
    target = repo / "src" / "pkg" / "probe.py"
    target.parent.mkdir(parents=True)
    target.write_text('x: int = "a"\n')

    result = _run(_MYPY_HOOK, _edit(target), repo / "sub")

    assert result.returncode == 2
    assert "probe.py:1: error" in result.stderr


def test_mypy_hook_is_quiet_on_clean_code_and_outside_src(repo: Path) -> None:
    """An edit outside src/ never runs mypy, even while src/ has a type error."""
    clean = repo / "src" / "pkg" / "probe.py"
    clean.parent.mkdir(parents=True)
    clean.write_text("x: int = 1\n")
    assert _run(_MYPY_HOOK, _edit(clean), repo).returncode == 0

    (repo / "src" / "pkg" / "broken.py").write_text('x: int = "a"\n')
    outside_src = repo / "tests" / "test_probe.py"
    outside_src.parent.mkdir()
    outside_src.write_text("x = 1\n")
    assert _run(_MYPY_HOOK, _edit(outside_src), repo).returncode == 0


@pytest.mark.parametrize("relative", ["src/app.py", "sub/AGENTS.md"])
def test_main_guard_blocks_repo_edits_on_main(main_repo: Path, relative: str) -> None:
    """Only the root AGENTS.md and CLAUDE.md are exempt."""
    result = _run(
        _MAIN_GUARD_HOOK,
        _edit(main_repo / relative),
        main_repo / "sub",
        CLAUDE_PROJECT_DIR=str(main_repo),
    )

    assert result.returncode == 2
    assert "BLOCKED" in result.stderr


@pytest.mark.parametrize("relative", ["AGENTS.md", "CLAUDE.md", "ignored/notes.txt"])
def test_main_guard_allows_agent_and_ignored_files_on_main(main_repo: Path, relative: str) -> None:
    result = _run(
        _MAIN_GUARD_HOOK,
        _edit(main_repo / relative),
        main_repo,
        CLAUDE_PROJECT_DIR=str(main_repo),
    )

    assert result.returncode == 0


def test_main_guard_follows_the_file_into_other_worktrees(main_repo: Path, tmp_path: Path) -> None:
    """The branch that counts is the edited file's checkout, not Claude's directory."""
    worktree = tmp_path.resolve() / "worktree"
    _git(main_repo, "worktree", "add", "-q", "-b", "feature", str(worktree))

    from_worktree = _run(
        _MAIN_GUARD_HOOK,
        _edit(main_repo / "src" / "app.py"),
        worktree,
        CLAUDE_PROJECT_DIR=str(main_repo),
    )
    into_worktree = _run(
        _MAIN_GUARD_HOOK,
        _edit(worktree / "src" / "app.py"),
        main_repo,
        CLAUDE_PROJECT_DIR=str(main_repo),
    )

    assert from_worktree.returncode == 2
    assert into_worktree.returncode == 0


def test_main_guard_resolves_symlinked_paths(main_repo: Path, tmp_path: Path) -> None:
    """A path through a symlink is matched by where git places it, not by its spelling."""
    link = tmp_path.resolve() / "link"
    link.symlink_to(main_repo, target_is_directory=True)

    code = _run(
        _MAIN_GUARD_HOOK, _edit(link / "src" / "app.py"), main_repo, CLAUDE_PROJECT_DIR=str(link)
    )
    agents = _run(
        _MAIN_GUARD_HOOK, _edit(link / "AGENTS.md"), main_repo, CLAUDE_PROJECT_DIR=str(link)
    )

    assert code.returncode == 2
    assert agents.returncode == 0


def test_main_guard_fails_closed_when_it_cannot_place_the_edit(
    main_repo: Path, tmp_path: Path
) -> None:
    """Git metadata, or a project directory git cannot read, blocks rather than passes."""
    nowhere = tmp_path.resolve() / "not-a-repo"
    nowhere.mkdir()

    git_metadata = [
        _run(
            _MAIN_GUARD_HOOK,
            _edit(main_repo / ".git" / name),
            main_repo,
            CLAUDE_PROJECT_DIR=str(main_repo),
        )
        for name in ("config", "AGENTS.md")
    ]
    unknown_project = _run(
        _MAIN_GUARD_HOOK,
        _edit(main_repo / "src" / "app.py"),
        nowhere,
        CLAUDE_PROJECT_DIR=str(nowhere),
    )

    assert [result.returncode for result in git_metadata] == [2, 2]
    assert unknown_project.returncode == 2


def test_main_guard_ignores_feature_branches_and_other_repos(repo: Path, tmp_path: Path) -> None:
    other = tmp_path.resolve() / "other"
    other.mkdir()
    _git(other, "init", "-q", "-b", "main")

    on_feature = _run(
        _MAIN_GUARD_HOOK, _edit(repo / "src" / "app.py"), repo, CLAUDE_PROJECT_DIR=str(repo)
    )
    elsewhere = _run(
        _MAIN_GUARD_HOOK, _edit(other / "notes.txt"), repo, CLAUDE_PROJECT_DIR=str(repo)
    )

    assert on_feature.returncode == 0
    assert elsewhere.returncode == 0


@pytest.mark.parametrize(
    "command",
    [
        "git commit -m x",
        "cd {repo} && git commit -m x",
        "git -C {repo} commit -m x",
        "git -c a=b commit -m x",
        "git --no-pager commit -m x",
        "git --attr-source HEAD commit -m x",
        "git add -A && git commit --amend --no-edit",
        "(cd {repo} && git commit)",
        "git commit;",
        "git commit>/dev/null",
        "/usr/bin/git commit -m x",
    ],
)
def test_commit_hook_blocks_commits_with_unformatted_files(repo: Path, command: str) -> None:
    (repo / "notes.md").write_text(_UNFORMATTED_MD)

    result = _run(_COMMIT_HOOK, _bash(command.format(repo=repo)), repo / "sub")

    assert result.returncode == 2
    assert "notes.md" in result.stderr


@pytest.mark.parametrize(
    "command",
    [
        "git log --grep commit",
        "git --no-pager log --grep commit",
        "git commit-tree HEAD^{tree} -m x",
        'git stash push -m "wip commit"',
        "ls .git/",
        "grep 'git commit' AGENTS.md",
    ],
)
def test_commit_hook_ignores_commands_that_do_not_commit(repo: Path, command: str) -> None:
    (repo / "notes.md").write_text(_UNFORMATTED_MD)

    assert _run(_COMMIT_HOOK, _bash(command), repo).returncode == 0


def test_commit_hook_passes_a_formatted_tree_from_a_subdirectory(repo: Path) -> None:
    (repo / "probe.py").write_text("x = 1\n")

    assert _run(_COMMIT_HOOK, _bash("git commit -m x"), repo / "sub").returncode == 0


@pytest.mark.parametrize(
    "command",
    [
        "git push --force",
        "git push origin feat --force",
        "git push -uf origin feat",
        "git push --force-with-lease",
        "git push origin +feat",
        "git push --mirror",
        "git push --all origin",
        "git push origin main",
        "git push -u origin main",
        "git push origin HEAD:main",
        "git push origin :main",
        "git -C . push --force",
        "git -c a=b push -f",
        "git --no-pager push origin main",
        "git --attr-source HEAD push -f",
        "/usr/bin/git push --force",
        "cd /x && git push origin main",
        "(cd /x && git push origin main)",
        "git push origin main;",
        'git push origin "main"',
        "git push origin m''ain",
        "git push origin 'HEAD:main'",
        'git push "--force" origin feat',
        "git \\\n  push --force origin feat",
        "git push origin ma\\in",
        "git push origin feat;git push origin main",
        "git push origin feat&git push --force",
        "git push --branches origin",
        "git push --force-w origin feat",
        "git push --mirro",
        "git push --al origin",
        "git push origin feat:heads/main",
        "git push origin HEAD:refs/heads/main",
        "git push origin :",
        "git push origin 'refs/heads/*'",
        "g''it push origin main",
        'git "push" origin main',
        "git p\\ush --force origin feat",
        "git $'push' --force origin feat",
    ],
)
def test_push_guard_blocks_force_pushes_and_pushes_to_main(
    repo: Path, shell: str, command: str
) -> None:
    result = _run(_PUSH_GUARD_HOOK, _bash(command), repo, shell=shell)

    assert result.returncode == 2
    assert "Direct push to main or force push" in result.stderr


@pytest.mark.parametrize(
    "command",
    [
        'git push origin "$BRANCH"',
        'git push -u origin "$(git branch --show-current)"',
        "git push origin `git branch --show-current`",
        "git --git-dir=/x/.git push origin feat",
        "git --work-tree /x push origin feat",
        "GIT_DIR=/x/.git git push origin feat",
        "env GIT_WORK_TREE=/x git push origin feat",
        "git -C /nonexistent/dir push origin feat",
        'git -c "user.name=A B" push origin feat',
        "git -C '/tmp/a b' push origin feat",
        'git -c "user.name=AB" push origin feat',
        "git push origin {main,feat}",
        "echo main | xargs git push origin",
        "git -c push.default=matching push",
        "X=matching git --config-env=push.default=X push",
        "GIT_CONFIG_PARAMETERS=\"'push.default=matching'\" git push",
        "git push origin $'\\x6dain'",
    ],
)
def test_push_guard_blocks_pushes_it_cannot_read(repo: Path, shell: str, command: str) -> None:
    """Expansions, quoted spaces, overrides, xargs and a missing -C dir hide where it goes."""
    result = _run(_PUSH_GUARD_HOOK, _bash(command), repo, shell=shell)

    assert result.returncode == 2
    assert "Cannot read this git push" in result.stderr


@pytest.mark.parametrize(
    "command",
    [
        "git push",
        "git push origin feat",
        "git push -u origin feat",
        "git push origin HEAD",
        "git push origin feature/maintenance",
        "git push origin main-fix",
        "git push origin v1.2.3",
        "git push origin --delete feat",
        'git push origin "feat"',
        "\\git push origin feat",
        "git push -o ci.skip origin feat",
        "git push --follow-tags origin feat",
        "git push --force-if-includes origin feat",
        "git push \\\n  origin feat",
        'git push origin feat && echo "$?"',
        'grep "git push origin main" AGENTS.md',
        "grep 'git push --force' AGENTS.md",
        'git commit -m "docs: explain git push origin main"',
        "git commit -F - <<'EOF'\nci: x\n\nThe guard's git push check reads quotes.\nEOF",
    ],
)
def test_push_guard_allows_feature_and_tag_pushes(repo: Path, shell: str, command: str) -> None:
    assert _run(_PUSH_GUARD_HOOK, _bash(command), repo, shell=shell).returncode == 0


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("git push", 2),
        ("git push -u origin", 2),
        ("git push origin HEAD", 2),
        ("git push origin @", 2),
        ("git push origin heads/main", 2),
        ("git push --tags origin HEAD", 2),
        ("git push --follow-tags", 2),
        ("git push -o ci.skip origin", 2),
        ("git push -uo ci.skip origin", 2),
        ("git push -o note=--tags", 2),
        ('git push -o "a b" origin', 2),
        ("git push --push-o ci.skip origin", 2),
        ("git push --recu check origin", 2),
        ("git push --recurse-submodules check origin", 2),
        ("git push origin :", 2),
        ("git push origin v1.2.3", 0),
        ("git push --tags", 0),
        ("git push origin feat", 0),
    ],
)
def test_push_guard_on_main_needs_a_named_target(
    main_repo: Path, shell: str, command: str, expected: int
) -> None:
    """On main, a push that names no branch, or names the current one, pushes main."""
    result = _run(_PUSH_GUARD_HOOK, _bash(command), main_repo, shell=shell)

    assert result.returncode == expected


def test_push_guard_reads_the_branch_of_the_checkout_named_by_dash_c(
    main_repo: Path, tmp_path: Path, shell: str
) -> None:
    root = tmp_path.resolve()
    worktree = root / "worktree"
    _git(main_repo, "worktree", "add", "-q", "-b", "feature", str(worktree))
    # Cut at its space, this path would name the feature worktree.
    on_main_with_space = root / "worktree two"
    _git(root, "init", "-q", "-b", "main", str(on_main_with_space))

    def push(command: str, cwd: Path) -> int:
        return _run(_PUSH_GUARD_HOOK, _bash(command), cwd, shell=shell, HOME=str(root)).returncode

    assert push(f"git -C {main_repo} push", worktree) == 2
    assert push(f'git -C "{main_repo}" push', worktree) == 2
    assert push(f"git -C {root} -C {main_repo.name} push", worktree) == 2
    assert push(f"git -C ~/{main_repo.name} push", worktree) == 2
    assert push(f'git -C "{on_main_with_space}" push', main_repo) == 2
    assert push(f"git -C {worktree} push", main_repo) == 0
    assert push(f"git -C {root} -C {worktree.name} push", main_repo) == 0
    assert push(f"git -C ~/{worktree.name} push", main_repo) == 0


def test_stop_hook_warns_once_about_unfinished_markers(tmp_path: Path) -> None:
    def stop(message: str, *, active: bool) -> subprocess.CompletedProcess[str]:
        payload = {"stop_hook_active": active, "last_assistant_message": message}
        return _run(_STOP_HOOK, payload, tmp_path)

    warned = stop("Left a TODO in the parser.", active=False)

    assert warned.returncode == 2
    assert "incomplete markers" in warned.stderr
    assert stop("Left a TODO in the parser.", active=True).returncode == 0
    assert stop("All done.", active=False).returncode == 0
