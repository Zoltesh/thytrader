# Shared lifecycle/risk accounting integration — 2026-10-06

The lifecycle and risk safety slices now share the durable unresolved-evidence predicates. New entries cannot pass merely because a newly created book has an opening balance, a previously verified midnight proof exists, or cash-only arithmetic shows prior profit.

- Same-quote retained books with unsettled reported executions or owned applied-but-unprojected inventory deny new risk. This also applies to pure gate calls without a price observation; independently proven membership, budget and exposure denials retain their existing precedence.
- Daily PnL, opening reconstruction and flat-day fill replay remain unknown for that evidence. No display mismatch string, stop/delete operation, cached product overlay, or old unowned exit can certify it away.
- Predicates do not provide executable quantities or repair financial rows. Other supported quote currencies remain separate; no FX equivalence is invented.
- Regressions include canceled unpublished partial executions, FILLED without reported quantity/fills, older exit evidence that cannot offset a later owned entry, applied positive cash with no projected position, prior genuine opening proof, today's and older books, both modes, absent observations, and a reloaded BTC-focused store with unresolved ETH inventory.
- Three old fixture scenarios now express their intended facts explicitly: an unfilled 60-quote reservation, an applied partial fill, and an unfilled canceled historical rate event. Production fail-closed checks were not relaxed to accommodate incomplete fixtures.

At this step, **203 focused tests passed** (risk, lifecycle-safety regressions, contract/schema and skill compatibility); Ruff, formatting and ty passed. Final full PostgreSQL, frontend, independent review and graph checks remain release gates.

The shared contract is now **thytrader-ops-contract-v68**, schema head **0069**. Chain: `0064 -> risk0065 -> 0066 -> 0067 -> 0068 -> 0069`. Migration0069 adds separate nullable verified-opening evidence with no historical backfill and refuses evidence-destroying downgrade. Migration0070 is unused. The operator schema is generated from source, not hand-maintained.

Reporting integration must separately expose unresolved inventory as unknown rather than flat/green or foreign; that cross-lane follow-up is still being verified. This document is not release certification or a production deployment record.
