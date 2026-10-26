# Testing — calyptus-project

## Layers

No stack yet: runners, commands and folders are filled in when the spec picks the stack.
For now only unit tests: integration and E2E are added when a ticket needs them.

| Layer       | Command | Needs |
| ----------- | ------- | ----- |
| Unit        | `uv run pytest` | nothing |
| Live smoke  | `RUN_LIVE=1 uv run pytest tests/test_predict.py::test_smoke_real_claude` | `claude` on PATH; makes one billed `claude -p` call |

`RUN_LIVE=1` enables tests that call the real `claude` CLI; without it they are skipped.
