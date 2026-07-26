# ThyTrader Operator Skill Placeholder

This directory intentionally does not contain `SKILL.md` yet.

The future skill will monitor and analyze a running ThyTrader instance through the stable, read-only operator API/CLI described in [`docs/agent-integration.md`](../../docs/agent-integration.md). It must not invent commands, scrape private internals, query PostgreSQL directly, or imply authority to modify trading state.

Creation gate:

- supported diagnostics API/CLI implemented;
- schemas and exit codes versioned;
- redaction verified;
- read-only behavior proven;
- end-to-end skill commands tested.
