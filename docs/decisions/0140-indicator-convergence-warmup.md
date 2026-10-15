# 0140: Indicator convergence warmup policy

- Status: Accepted
- Date: 2026-10-14
- Relates to: [0082](0082-strategy-root-mutable-strategies-auto-snapshots.md),
  [0086](0086-indicator-catalog-expansion-and-offset.md),
  [0096](0096-reference-instruments.md), [0113](0113-deploy-anchored-window-cache.md)

## Context

Recursive indicators are seeded and then decay toward their established value. In this codebase an
EMA(n) is seeded with the SMA of its first `n` inputs and updated with α = 2/(n+1); Wilder RSI, ATR
and ADX use α = 1/n; KAMA starts from a source value with a coefficient between the squares of its
fast and slow constants. Strategies declare `data_requirements.warmup_bars`, which validation only
compared with each indicator's *minimum* warmup: the bar of its first value. For EMA(200) that is
200 bars, at which point the series is still essentially its seed.

Every consumer seeds from that declared or derived count:

- research decision clocks seed `warmup_bars` before the run's evaluation start;
- live and paper decision clocks seed `warmup_bars` before the deployment's creation (ADR 0113 keeps
  that boundary, so the decision-clock prefix grows with the deployment's age);
- live HTF filters, extra indicator timeframes and reference instruments select exactly their
  warmup ending at the latest decision bar, a moving window, while research selects it relative to
  the run start; for an EMA(n) whose derived warmup is `n`, that live value is an SMA(n) every bar.

Research and live therefore evaluate the same rules on differently seeded series, and both depend
on an arbitrary anchor. In a synthetic example, an EMA(50)/EMA(200) "fresh golden cross" appears
under a seed placed 200 bars before the cross and does not exist on the established series. A
review of live trading found entries of exactly this kind, and a daily EMA(100) reference gate that
live evaluated as an SMA(100).

## Decision

1. **Convergence warmup per indicator** (`strategies/schema/indicator_convergence.py`): the minimum
   warmup plus, for every recursive stage, `ceil(log(ε) / log(1 − α))` updates with ε = 1e-3:
   EMA, force index (α = 2/(n+1)); DEMA, TEMA and TRIX two or three chained EMA stages; MACD and PPO
   the slow EMA then the signal EMA (fast and slow run in parallel); TSI the long, short and signal
   EMAs; KAMA its squared slow constant; RSI, ATR, NATR and Supertrend's ATR one Wilder stage; ADX
   two; Stochastic RSI its RSI stage; Keltner the later of its EMA and Wilder ATR branches. Exact
   rolling windows (SMA, WMA, HMA, Bollinger, Donchian, CMO, percent rank, …) equal their minimum.
   Supertrend direction, Parabolic SAR trend and OBV/A-D levels are `anchor_sensitive`: their state
   depends on the first bar for any warmup (OBV/A-D crossovers with their own signal are not).
2. **An explicit, snapshotted policy.** `DataRequirements.warmup_policy = "convergence"` (omitted
   when absent, so existing canonical bytes and fingerprints are unchanged) makes every clock use
   convergence warmup: validation requires declared decision and HTF warmups to cover it, and the
   derived warmups of extra indicator timeframes and reference instruments are computed with it.
   Absent, every consumer keeps the historical minimum-warmup meaning, so stored results, their
   verification, replay packages and running books are untouched.
3. **New and saved documents adopt it.** Saving, creating, importing or cloning a strategy, and every
   template draft, materializes the policy and raises declared warmups to the convergence values
   (never lowers them). A document whose requirement exceeds the declarable 10,000 bars keeps no
   policy rather than a silently clipped one. Loading never materializes.
4. **Sweeps follow the base.** Parameter-sweep candidates of a policy base cover their own
   substituted periods' convergence; candidates of an older base keep the minimum rule, so
   historical study requests regenerate identical candidates.
5. **The guarantee is a bound, not identity.** Converged series differ from an infinitely
   established series by at most ε times their initial seed error. That removes seed-made
   crossovers but can still flip a comparison sitting exactly on its threshold; exact signal identity
   would need a shared initialization point and is not promised.

6. **Starts and resumes check the policy** (`execution/warmup_gate.py`). As first accepted, paper
   and live starts and operator resumes refused a snapshot without it (`WARMUP_POLICY_REQUIRED`).
   [ADR 0142](0142-opt-in-start-safeguards.md) supersedes that default: a start adopts the policy
   by re-saving the strategy, a snapshot that still lacks it starts or resumes with a
   `WARMUP_POLICY_MISSING` warning, and refusal is an opt-in risk-policy safeguard. Pause and stop
   are never affected; running books keep running; discretionary books are exempt.
7. **Reference watches must cover the warmup.** A reference series' enabled watch must look back
   at least its warmup plus one bar (capped at the interval's ceiling); a watch sized for the old
   minimum no longer passes the start gate.

8. **A start checks venue history** (`execution/clock_windows.py`, `execution/start_history.py`).
   A book fetches every clock's window (decision clock per covered product, HTF filter, extra
   indicator timeframes, references) from the venue on demand, starting a full warmup before its
   creation, so an established market is warmed up from its first decision. Only a market listed
   after a window start lacks those candles. Every start (single, adoption, each portfolio
   sleeve during planning) therefore reads each series' own clock up to its window's first bar
   (one bounded request); when that read is empty, as on a thin market, it reads the previous
   year of daily bars, counting only a day that closed by the window start. It reports
   `HISTORY_TOO_SHORT` when the series has not traded by then, or `HISTORY_UNCONFIRMED` when the
   venue cannot be read: a warning by default, a refusal under the opt-in safeguard
   ([ADR 0142](0142-opt-in-start-safeguards.md)). The worker sizes its windows with the
   same functions, so the check and the fetch agree.
9. **The worker never enters on a short window.** Under the policy, an entry-eligible bar is skipped
   as `warmup`, with that reason, while its decision window holds fewer bars than its warmup or an
   HTF or extra-timeframe window starts after the evaluator's required coverage for that bar, which
   includes the previous mapped bar at a rollover (references already require theirs). After a
   passed start check it happens only briefly, when an established market had no trade on a window's
   first bar (the probe saw earlier candles; the window starts after the quiet bar until the
   required coverage moves past it); otherwise it guards books started without the check.

10. **Visible where indicators are chosen.** The indicator catalog (operator `indicators` report and
    the generated browser catalog) carries each kind's `convergence` class, its default convergence
    warmup and `anchor_sensitive`; the builder shows both the first-value and the settled bar count,
    computed by TypeScript formulas cross-checked against schema-computed examples.

11. **Decision rows record their windows.** Each evaluated strategy bar journals, per clock it read
    (its product's decision clock, HTF filter and extra timeframes, plus the references), the role,
    series, warmup, the deploy-anchored window start and the first bar the venue returned, so a seed
    can be reproduced and a window whose history starts late (a younger market, or no trades at
    first) is visible on every row, managed-stopped signal exits included. Journaling is evidence
    only: a failure to build the windows is logged and never interrupts the cycle.

## Consequences

- More history is needed before the first decision: EMA(200) on a 2h clock needs 891 bars (about
  74 days), a daily EMA(100) reference 446 days. Datasets and watches that cannot cover it fail
  closed with coverage errors; products with shorter history cannot host such strategies.
- Saving an older strategy changes its document and fingerprint; its new backtests differ from its
  old ones. Old results remain valid records of what they computed, under the old policy.
- Template blueprints and examples advertise converged warmups.

## Alternatives considered

- **Override the warmup at every consumer.** Rejected: more than forty read sites, run
  specifications that must equal the snapshot, and verifiers that recompute derived warmups would
  all change meaning for existing snapshots.
- **Switch the derived-warmup helper globally.** Rejected: it would change running books' reference
  and HTF values on the next cycle and the meaning of stored runs whose verification recomputes it.
- **A canonical sliding window for every decision.** Gives exact research/live agreement but changes
  strategy semantics, supersedes ADR 0113 and multiplies backtest compute; deferred.
