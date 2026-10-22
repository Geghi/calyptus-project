# Coding standards

How code in this repo must be written.
Enforced at review: `/ship`'s standards reviewer checks every diff against it.
A fixed pattern (banned API, import shape) is better as a lint rule or hook; keep here what needs judgement.
Workflow rules (TDD, commands, git, docs) live in `CLAUDE.md`, not here.
One section per rule: **the rule**, *why* (the failure it prevents), and where the canonical example lives.
Sections are added as the code establishes conventions.

## No comments or docstrings

Write code that reads without comments: better names, smaller functions, explicit types.
No comments and no docstrings in source files.
Only exceptions: a targeted tool directive with its one-line reason (`# noqa: E501  # <why>`, `# pyright: ignore[rule]`, `// eslint-disable-next-line rule`), and a docstring a framework reads at runtime (e.g. an LLM tool description).
Knowledge that needs prose goes in docs, ADRs or `CONTEXT.md`.
Config files (YAML, TOML, Dockerfile) keep `#` comments.
Enforce it with an architecture test once the stack exists (reference: `comment_problems` in `prospector/backend/tests/architecture/test_architecture.py`).

**Why:** comments drift from the code and lie; a name the reader sees on every use can't.

## Secrets never in logs, errors or tracebacks

A secret (API key, token, password) travelling in a URL or header never reaches a log line, an exception message or a traceback.
Check what the HTTP client library logs by itself (request URLs at DEBUG, headers in retries, the URL inside its exception text) and silence or redact it.
Prefer headers over query strings; when a query secret is unavoidable, strip it before the URL is logged or raised.

**Why:** a key in a log or a stored error is leaked to everyone who reads them, and the library's own logging leaks it without any line of ours.
