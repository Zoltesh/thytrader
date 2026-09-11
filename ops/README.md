# ops — operate a running ThyTrader instance

Open **this folder** as the Cursor workspace when diagnosing, ingesting, researching, or controlling
paper/live. Do not open the git root for that work; the git root is for contributors who change
source and run GitNexus.

The repository root is the parent of this folder. Run every `uv run thytrader-*` command from that
root. Skills live in `../skills/` and are symlinked from `.cursor/skills/` so Cursor auto-discovers
them here.

Rebuild or restart the stack with `make run` from the repository root only when the user asked, or
when operator health / HTTP reports a stale Compose image (version mismatch, or 404 on agent routes
while `/health/ready` is 200). Apply migration `0016` as part of that rebuild.

See `AGENTS.md` in this folder for the hard stop.
