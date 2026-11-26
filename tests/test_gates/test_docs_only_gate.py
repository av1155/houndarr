"""Consolidated invariant: docs-only PRs skip required jobs, and nothing else does.

Security, Dependency Review, Docker and Security Smoke Test run on every PR and
skip their required jobs by ``if:`` when ``docs-only.yml`` classifies the PR as
docs-only.  A skipped job counts as passing, so a classifier that says "true"
too often lets a code change merge untested.  ci-skip.yml used to post passing
stub checks whenever any file was docs, and failed on fork PRs (#849).

The classifier tests run the workflow's own script under node with a mocked
GitHub client; the workflow tests read the YAML as text, since PyYAML is not a
declared dependency.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

_WORKFLOWS = Path(__file__).resolve().parents[2] / ".github" / "workflows"
_CLASSIFIER = _WORKFLOWS / "docs-only.yml"

_DOCS_ONLY = "needs.changes.result == 'success' && needs.changes.outputs.docs_only == 'true'"
_GATE = f"!({_DOCS_ONLY})"
_CHANGES_JOB = (
    "  changes:\n"
    "    name: Docs-only check\n"
    "    if: github.event_name == 'pull_request'\n"
    "    uses: ./.github/workflows/docs-only.yml\n"
    "    permissions:\n"
    "      pull-requests: read\n"
    "\n"
)
_GATED_JOBS = {
    "security.yml": ["dependency-audit", "sast", "trivy-fs"],
    "dependency-review.yml": ["dependency-review"],
    "security-smoke-test.yml": ["smoke-test"],
    "docker.yml": ["scan"],
}

_HARNESS = r"""
const input = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const outputs = {};
const logs = [];
const pages = [];
const core = {
  info: (message) => logs.push(String(message)),
  setOutput: (name, value) => { outputs[name] = value; },
};
const listed = input.files.slice(0, 3000);
let headSha = input.event.head.sha;
const github = {
  rest: {
    pulls: {
      listFiles: async ({ page, per_page }) => {
        pages.push({ page, per_page });
        if (input.headMovesDuringListing) headSha = 'b'.repeat(40);
        return { data: listed.slice((page - 1) * per_page, page * per_page) };
      },
      get: async () => {
        if (input.getFails) throw new Error('pulls.get failed');
        return { data: { ...input.pr, head: { sha: headSha } } };
      },
    },
  },
  paginate: async (method, params) => {
    const all = [];
    for (let page = 1; ; page += 1) {
      const { data } = await method({ ...params, page });
      all.push(...data);
      if (data.length < params.per_page) return all;
    }
  },
};
const context = {
  repo: { owner: 'av1155', repo: 'houndarr' },
  payload: { pull_request: input.event, repository: { default_branch: 'main' } },
};
const AsyncFunction = Object.getPrototypeOf(async () => {}).constructor;
new AsyncFunction('github', 'context', 'core', input.script)(github, context, core).then(
  () => process.stdout.write(JSON.stringify({ outputs, logs, pages, error: null })),
  (error) => process.stdout.write(JSON.stringify({ outputs, logs, pages, error: String(error) })),
);
"""


def _script() -> str:
    """Return the classifier's github-script body, dedented."""
    lines = _CLASSIFIER.read_text().splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip() == "script: |") + 1
    indent = len(lines[start]) - len(lines[start].lstrip())
    body = []
    for line in lines[start:]:
        if line.strip() and len(line) - len(line.lstrip()) < indent:
            break
        body.append(line[indent:])
    return "\n".join(body)


def _file(filename: str, previous: str | None = None) -> dict[str, str]:
    entry = {"filename": filename, "status": "renamed" if previous else "modified"}
    if previous:
        entry["previous_filename"] = previous
    return entry


def _classify(
    files: list[dict[str, str]],
    *,
    changed_files: int | None = None,
    event_base: str = "main",
    pr_base: str = "main",
    head_moves_during_listing: bool = False,
    get_fails: bool = False,
    tmp_path: Path,
) -> dict[str, Any]:
    """Run the classifier script against a mocked PR and return what it did."""
    if changed_files is None:
        changed_files = len({f["filename"] for f in files})
    event = {"number": 1, "head": {"sha": "a" * 40}, "base": {"ref": event_base}}
    pr = {"base": {"ref": pr_base}, "changed_files": changed_files}
    harness = tmp_path / "harness.js"
    harness.write_text(_HARNESS)
    payload = {
        "script": _script(),
        "files": files,
        "event": event,
        "pr": pr,
        "headMovesDuringListing": head_moves_during_listing,
        "getFails": get_fails,
    }
    result = subprocess.run(
        ["node", str(harness)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    return json.loads(result.stdout)


_needs_node = pytest.mark.skipif(
    shutil.which("node") is None and not os.environ.get("CI"),
    reason="the classifier tests run the workflow script under node",
)


@_needs_node
@pytest.mark.parametrize(
    ("files", "expected"),
    [
        pytest.param([_file("docs/guide.md"), _file("website/src/page.tsx")], "true", id="docs"),
        pytest.param([_file("docs/guide.md"), _file("src/houndarr/app.py")], "false", id="mixed"),
        pytest.param([_file("docs/app.py", "src/houndarr/app.py")], "false", id="rename-from-src"),
        pytest.param([_file("docs/new.md", "docs/old.md")], "true", id="rename-within-docs"),
        pytest.param([_file("README.md")], "true", id="root-markdown"),
        pytest.param([_file(".github/PULL_REQUEST_TEMPLATE.md")], "true", id="dot-dir-markdown"),
        pytest.param([_file("README.MD")], "false", id="case-sensitive"),
        pytest.param([_file("READMExmd")], "false", id="literal-dot-in-md"),
        pytest.param([_file("xclaude/settings.json")], "false", id="literal-dot-in-claude"),
        pytest.param([_file("scripts/run.cmd")], "false", id="script-outside-marketing"),
        pytest.param([_file("scripts/marketing/capture.py")], "true", id="marketing-script"),
        pytest.param([_file("docs/images/shot.png")], "true", id="nested-docs-asset"),
        pytest.param([_file("website")], "false", id="bare-directory-name"),
        pytest.param([_file("src/docs/notes.py")], "false", id="anchored-at-start"),
        pytest.param([_file("CHANGES.md.bak")], "false", id="anchored-at-end"),
        pytest.param([], "false", id="no-files"),
    ],
)
def test_classifies_changed_paths(
    files: list[dict[str, str]], expected: str, tmp_path: Path
) -> None:
    outcome = _classify(files, tmp_path=tmp_path)
    assert outcome["error"] is None
    assert outcome["outputs"] == {"docs_only": expected}


@_needs_node
@pytest.mark.parametrize(
    "path",
    [
        "Dockerfile",
        ".dockerignore",
        "entrypoint.sh",
        "pyproject.toml",
        "uv.lock",
        "hatch_build.py",
        "package.json",
        "pnpm-lock.yaml",
        "pnpm-workspace.yaml",
        "VERSION",
        ".trivyignore.yaml",
        ".github/workflows/security.yml",
        "charts/houndarr/values.yaml",
        "scripts/security_smoke_test.sh",
        "src/houndarr/templates/base.html",
        "src/houndarr/static/img/houndarr-logo.png",
        "tests/test_gates/test_docs_only_gate.py",
    ],
)
def test_code_and_build_config_are_never_docs_only(path: str, tmp_path: Path) -> None:
    outcome = _classify([_file(path)], tmp_path=tmp_path)
    assert outcome["outputs"] == {"docs_only": "false"}


@_needs_node
def test_removed_source_file_is_not_docs_only(tmp_path: Path) -> None:
    removed = {"filename": "src/houndarr/legacy.py", "status": "removed"}
    outcome = _classify([_file("docs/guide.md"), removed], tmp_path=tmp_path)
    assert outcome["outputs"] == {"docs_only": "false"}


@_needs_node
def test_symlink_type_change_listed_twice_counts_once(tmp_path: Path) -> None:
    files = [_file("docs/latest"), _file("docs/latest")]
    outcome = _classify(files, changed_files=1, tmp_path=tmp_path)
    assert outcome["outputs"] == {"docs_only": "true"}


@_needs_node
@pytest.mark.parametrize(
    "files",
    [[_file("docs/guide.md")], [_file("docs/guide.md"), _file("docs/guide.md")]],
    ids=["one-missing", "duplicate-hides-a-missing-path"],
)
def test_listing_shorter_than_changed_files_is_not_docs_only(
    files: list[dict[str, str]], tmp_path: Path
) -> None:
    outcome = _classify(files, changed_files=2, tmp_path=tmp_path)
    assert outcome["outputs"] == {"docs_only": "false"}


def _docs_only_globs() -> list[str]:
    declaration = re.search(r"^const DOCS_ONLY_GLOBS = \[(.*)\];$", _script(), re.MULTILINE)
    assert declaration
    return re.findall(r"'([^']*)'", declaration.group(1))


def test_patterns_use_only_glob_syntax_the_classifier_translates() -> None:
    globs = _docs_only_globs()
    assert globs
    for glob in globs:
        assert re.fullmatch(r"[\w./*-]+", glob), f"{glob!r} needs syntax toRegExp lacks"


def test_browser_e2e_ignores_exactly_the_docs_only_paths() -> None:
    text = (_WORKFLOWS / "browser-e2e.yml").read_text()
    block = re.search(r"^    paths-ignore:\n((?: {6}- .*\n)+)", text, re.MULTILINE)
    assert block
    ignored = re.findall(r'^ {6}- "([^"]*)"$', block.group(1), re.MULTILINE)
    assert ignored == _docs_only_globs()


@_needs_node
@pytest.mark.parametrize("changed_files", [3000, 3500])
def test_listing_cap_is_not_docs_only(changed_files: int, tmp_path: Path) -> None:
    files = [_file(f"docs/page-{n}.md") for n in range(changed_files)]
    outcome = _classify(files, changed_files=changed_files, tmp_path=tmp_path)
    assert outcome["outputs"] == {"docs_only": "false"}


@_needs_node
@pytest.mark.parametrize(("last", "expected"), [("docs/last.md", "true"), ("src/last.py", "false")])
def test_reads_every_page(last: str, expected: str, tmp_path: Path) -> None:
    files = [_file(f"docs/page-{n}.md") for n in range(249)] + [_file(last)]
    outcome = _classify(files, tmp_path=tmp_path)
    assert outcome["outputs"] == {"docs_only": expected}
    assert outcome["pages"] == [{"page": n, "per_page": 100} for n in (1, 2, 3)]


@_needs_node
def test_head_moved_during_listing_is_not_docs_only(tmp_path: Path) -> None:
    outcome = _classify([_file("docs/guide.md")], head_moves_during_listing=True, tmp_path=tmp_path)
    assert outcome["outputs"] == {"docs_only": "false"}


@_needs_node
@pytest.mark.parametrize(
    ("event_base", "pr_base"),
    [("release", "release"), ("main", "release"), ("release", "main")],
)
def test_base_other_than_default_branch_is_not_docs_only(
    event_base: str, pr_base: str, tmp_path: Path
) -> None:
    outcome = _classify(
        [_file("docs/guide.md")], event_base=event_base, pr_base=pr_base, tmp_path=tmp_path
    )
    assert outcome["outputs"] == {"docs_only": "false"}


@_needs_node
def test_api_failure_sets_no_output(tmp_path: Path) -> None:
    outcome = _classify([_file("docs/guide.md")], get_fails=True, tmp_path=tmp_path)
    assert outcome["error"] is not None
    assert outcome["outputs"] == {}


@_needs_node
def test_paths_never_reach_the_log(tmp_path: Path) -> None:
    crafted = "docs/##[error]x.md"
    outcome = _classify([_file(crafted), _file("src/::warning::y.py")], tmp_path=tmp_path)
    assert outcome["outputs"] == {"docs_only": "false"}
    assert outcome["logs"]
    assert not any("##[" in log or "::" in log for log in outcome["logs"])


def _jobs(text: str) -> dict[str, str]:
    """Split a workflow's ``jobs:`` mapping into ``{job_id: job_block}``."""
    body = text.split("\njobs:\n", 1)[1]
    return dict(re.findall(r"^  ([\w-]+):\n((?:(?: {4}.*)?\n)*)", body, re.MULTILINE))


def _workflow_jobs(workflow: str) -> dict[str, str]:
    return _jobs((_WORKFLOWS / workflow).read_text())


def _key(job: str, key: str) -> str:
    """Return a job-level key's value, joining a folded ``>-`` scalar onto one line."""
    match = re.search(rf"^    {key}: ?(.*)\n((?: {{6}}.*\n)*)", job, re.MULTILINE)
    assert match, f"no job-level {key}"
    value, continuation = match.groups()
    if value == ">-":
        return " ".join(line.strip() for line in continuation.splitlines())
    return value


@pytest.mark.parametrize("workflow", sorted(_GATED_JOBS))
def test_caller_runs_on_every_pr_and_calls_the_classifier(workflow: str) -> None:
    text = (_WORKFLOWS / workflow).read_text()
    assert not re.search(r"^\s*paths(-ignore)?:", text, re.MULTILINE)
    assert "\n" + _CHANGES_JOB in text


@pytest.mark.parametrize(
    ("workflow", "job_id"),
    [(workflow, job_id) for workflow, job_ids in _GATED_JOBS.items() for job_id in job_ids],
)
def test_required_job_skips_only_on_a_successful_docs_only_result(
    workflow: str, job_id: str
) -> None:
    job = _workflow_jobs(workflow)[job_id]
    assert "changes" in _key(job, "needs")
    condition = _key(job, "if")
    assert condition.startswith("${{ !cancelled() && ")
    assert _GATE in condition
    if workflow != "docker.yml":
        assert condition == f"${{{{ !cancelled() && {_GATE} }}}}"


def test_build_reports_on_docs_only_prs_and_skips_its_steps() -> None:
    build = _workflow_jobs("docker.yml")["build"]
    assert "${{" in _key(build, "name")
    assert _key(build, "needs") == "changes"
    assert _key(build, "if").startswith("${{ !cancelled() && ")
    assert "docs_only" not in _key(build, "if")
    assert f"      DOCS_ONLY: ${{{{ {_DOCS_ONLY} }}}}\n" in build
    steps = build.split("\n      - ")[1:]
    assert len(steps) == 8
    for step in steps:
        condition = re.search(r"^        if: (.*)$", step, re.MULTILINE)
        assert condition, f"step {step.splitlines()[0]!r} has no if"
        clause = condition.group(1)
        assert (
            "env.DOCS_ONLY != 'true'" in clause or "github.event_name != 'pull_request'" in clause
        ), f"step {step.splitlines()[0]!r} runs on docs-only PRs"


def test_manual_runs_never_take_the_release_path() -> None:
    text = (_WORKFLOWS / "docker.yml").read_text()
    build = _jobs(text)["build"]
    assert (
        "(github.event_name != 'workflow_dispatch' || startsWith(github.ref, 'refs/heads/'))"
        in (_key(build, "if"))
    )
    group = re.search(r"^concurrency:\n(?:  #.*\n)*  group: (.*)$", text, re.MULTILINE)
    assert group
    assert "github.event_name" in group.group(1)


def test_scan_waits_for_a_successful_build() -> None:
    scan = _workflow_jobs("docker.yml")["scan"]
    assert _key(scan, "needs") == "[changes, build]"
    assert "needs.build.result == 'success'" in _key(scan, "if")


def test_computed_job_names_are_never_skipped_as_docs_only() -> None:
    for workflow in sorted(_WORKFLOWS.glob("*.yml")):
        for job_id, job in _jobs(workflow.read_text()).items():
            name = re.search(r"^    name: (.*)$", job, re.MULTILINE)
            if name and "${{" in name.group(1) and re.search(r"^    if:", job, re.MULTILINE):
                assert "docs_only" not in _key(job, "if"), f"{workflow.name}: {job_id}"


def test_classifier_has_no_concurrency_group_and_reads_only_pull_requests() -> None:
    text = _CLASSIFIER.read_text()
    assert not re.search(r"^\s*concurrency:", text, re.MULTILINE)
    assert "\npermissions: {}\n" in text
    assert "    permissions:\n      pull-requests: read\n    outputs:\n" in text


def test_classifier_output_reaches_the_callers() -> None:
    text = _CLASSIFIER.read_text()
    assert "        value: ${{ jobs.classify.outputs.docs_only }}\n" in text
    assert "      docs_only: ${{ steps.classify.outputs.docs_only }}\n" in text
    assert "        id: classify\n" in text
    assert "core.setOutput('docs_only', " in _script()


def test_no_workflow_posts_stub_check_runs() -> None:
    assert not (_WORKFLOWS / "ci-skip.yml").exists()
    for workflow in _WORKFLOWS.glob("*.yml"):
        assert "checks.create" not in workflow.read_text(), workflow.name
