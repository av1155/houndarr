---
description: Run tests with various options
argument-hint: "[full|file:<path>|keyword:<expr>|coverage]"
allowed-tools: Bash(.venv/bin/pytest *), Bash(.venv/bin/python -m pytest *)
---

# Run Tests

Parse the argument and run the appropriate test command.

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
.venv/bin/pytest $ARGUMENTS -v
```

## Reporting

Report pass/fail clearly. If there are failures, show the failing test
names and short tracebacks. Do not recite pass counts unless there are
failures to contextualize.
