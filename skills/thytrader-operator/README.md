# ThyTrader Operator Skill

Read-only diagnostics for a running instance. See [`SKILL.md`](SKILL.md).

Creation gate (met):

- versioned operator API/CLI implemented (`thytrader-operator`, `GET /api/v1/operator/...`);
- `schema_version` is `thytrader-operator-report-v1`;
- redaction and non-mutation tests exist;
- documented commands are covered by contract tests;
- skill paths and schema version are compatibility-checked against the application.
