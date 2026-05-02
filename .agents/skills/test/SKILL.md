---
name: test
description: Run Houndarr tests with various options (full suite, single file, keyword filter, coverage). Use when the user asks to run tests, run pytest, check test status, or invokes /test. Trigger phrases include run tests, test, pytest, coverage.
---

# Run Tests

Parse the user's argument and run the appropriate test command. Argument forms: `[full|file:<path>|keyword:<expr>|coverage]` or a bare path.

## Dispatch

- **No argument or `full`**: run the full test suite.

```
.venv/bin/pytest
```

- **`file:<path>`**: run a single test file with verbose output.

```
.venv/bin/pytest <path> -v
```

- **`keyword:<expr>`**: run tests matching a keyword expression.

```
.venv/bin/pytest -k "<expr>" -v
```

- **`coverage`**: run with coverage reporting.

```
.venv/bin/pytest --cov=houndarr --cov-report=term-missing
```

- **Bare path** (no prefix): treat as a direct pytest path argument.

```
.venv/bin/pytest <path> -v
```

## Reporting

Report pass/fail clearly. If there are failures, show the failing test names and short tracebacks. Do not recite pass counts unless there are failures to contextualize.
