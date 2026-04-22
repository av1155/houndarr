# Houndarr developer ergonomics.
#
# Wraps the five local quality gates documented in AGENTS.md plus a
# handful of frequent workflows so contributors do not have to memorise
# the exact invocations.  Install just via `brew install just` (macOS)
# or `cargo install just`; without just, the commands in AGENTS.md
# remain the source of truth.
#
# Usage:
#     just            # list all targets
#     just check      # run every gate: lint + format + type + sast + full pytest
#     just quick      # fast feedback loop: lint + type + non-integration pytest
#     just fix        # apply ruff auto-fixes and reformat

python := ".venv/bin/python"
pytest := ".venv/bin/pytest"

# Default target: list every recipe.
default:
    @just --list

# Run all five gates in CI order. Matches the mandatory sequence in AGENTS.md.
check: lint fmt-check type sec test

# Fast feedback loop: skip format-check, bandit, and integration tests.
quick: lint type test-quick

# Individual gates
lint:
    {{python}} -m ruff check src/ tests/

fmt-check:
    {{python}} -m ruff format --check src/ tests/

type:
    {{python}} -m mypy src/

sec:
    {{python}} -m bandit -r src/ -c pyproject.toml

test:
    {{pytest}}

test-quick:
    {{pytest}} -m "not integration"

test-integration:
    {{pytest}} -m integration tests/test_e2e/

# Run only characterisation (pinning) tests: the Track A safety net that
# locks current behaviour before a refactor batch touches its module.
pin:
    {{pytest}} -m pinning

# Run the full suite in parallel across available CPUs (pytest-xdist).
test-parallel:
    {{pytest}} -n auto

# Apply ruff auto-fixes and reformat in place.
fix:
    {{python}} -m ruff check --fix src/ tests/
    {{python}} -m ruff format src/ tests/

# Start the dev server against ./data-dev with hot-reload and debug logs.
dev:
    {{python}} -m houndarr --data-dir ./data-dev --dev

# Browser e2e against a Docker-Compose stack. Assumes the image is built
# and mock-sonarr / mock-radarr are reachable on the shared network.
# Host env: HOUNDARR_URL, MOCK_SONARR_URL, MOCK_RADARR_URL, HOUNDARR_E2E_USER,
# HOUNDARR_E2E_PASS.
test-browser browser="chromium":
    {{pytest}} tests/e2e_browser/ --confcutdir tests/e2e_browser --browser {{browser}} -q

# Print the commit history since the branch last matched main.
log:
    git log --oneline main..HEAD
