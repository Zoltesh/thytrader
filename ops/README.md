# ops — operate a running ThyTrader instance

Open **this folder** as the Cursor workspace when diagnosing, ingesting, researching, or controlling
paper/live. Do not open the git root for that work; the git root is for contributors who change
source and run GitNexus.

The repository root is the parent of this folder. Run every `uv run thytrader-*` command from that
root. Skills live in `../skills/` and are symlinked from `.cursor/skills/` so Cursor auto-discovers
them here.

Every HTTP agent CLI checks the full `/health/ready` ops contract before its command. Rebuild or
restart the stack with `make run` from the repository root only when the user asked, or when a CLI
reports the single stale-image signal: version or ops-contract mismatch, or 404 on an agent route
while `/health/ready` is 200. Matching `0.1.0` alone is not evidence that the image is current. Apply
migration `0026` as part of that rebuild.

For data loops, `complete` describes only the current contiguous published island.
`watch_complete` is the completion decision: it must be true before the configured watch lookback is
done. When false, use `thytrader-data inspect-gaps`; its classified holes cover the full watch window
and are never interpolated.

See `AGENTS.md` in this folder for the hard stop.
