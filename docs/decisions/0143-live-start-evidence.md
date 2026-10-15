# 0143: Record the evidence behind every live start; refusing thin evidence is opt-in

- Status: Accepted
- Date: 2026-10-15
- Relates to: [0142](0142-opt-in-start-safeguards.md),
  [0044](0044-parameter-sweeps-wfo-stitched-equity.md)

## Context

Nothing recorded what a strategy had shown when someone took it live. A strategy could go
live straight from the builder, with no out-of-sample study and no paper run, and later
nobody could tell whether that was a considered decision. ThyTrader gives its user control
(ADR 0142): it should make the evidence visible and say when it is thin, not decide for the
user whether they may trade.

## Decision

1. **Every live start records its evidence.** A live start (a single bot, adopt into bot, or a
   live portfolio sleeve) gathers what its exact strategy fingerprint has shown:
   - the out-of-sample windows of catalog studies (how many, their trades, their summed net
     result; sweep-scored and full windows never count, as in promotion evidence);
   - the paper bots of the same fingerprint (how many, and the days since the first was
     created).

   The record (`thytrader-live-start-evidence-v1`) is kept in `live_start_evidence`, one row
   per live deployment, first record kept (Alembic 0079). It is returned on the start response
   as `live_evidence` and read later with `GET /api/v1/deployments/{id}/live-evidence`
   (`thytrader-runtime live-evidence ID`).
2. **Thin evidence warns.** Below the thresholds (default: 30 out-of-sample trades, a positive
   out-of-sample net result, 14 days since the first paper bot) the start goes ahead with
   `LIVE_EVIDENCE_THIN: …` naming each gap; a sleeve carries it in its outcome message. A
   source that cannot be read counts as thin when a threshold needs it. Thresholds the operator
   sets to zero (and a non-positive result allowed) need no research or paper at all.
3. **Refusing is opt-in.** The risk policy's `start_safeguards.require_live_evidence` refuses a
   thin live start with 409 `LIVE_EVIDENCE_REQUIRED` (portfolio problem
   `live_evidence_required`) before anything is created; `start_safeguards.live_evidence` sets
   the thresholds. Both are omitted from the policy's canonical bytes while off or default.
4. **Exact fingerprint only.** Evidence of another revision of the strategy is not this
   strategy's evidence: an edit, or the warmup adoption of ADR 0142, starts a new record.

## Consequences

- Every live bot started from now on answers "what had this shown when it went live?".
- Strategies re-saved since their studies or paper runs read as thin until the new revision is
  studied or paper traded; the start still goes ahead unless the operator opted in to refuse.
- Resuming a live bot reads nothing new; the evidence belongs to its start.
- A failure to store the record never undoes a started bot: the response warns
  `LIVE_EVIDENCE_NOT_RECORDED` and still carries the evidence.

## Alternatives considered

- **Refuse thin live starts by default.** Rejected: it would keep a new user from trading their
  own strategy (ADR 0142).
- **A durable alert per thin live bot.** Alerts are supervision findings recomputed each cycle;
  a one-off start record fits a table read on demand better.
