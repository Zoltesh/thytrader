# Strategy-version workspace: implementation-ready design specification

> **Status:** implementation handoff
>
> **Source reviewed:** `main` at `50f41196d0a820e11d686f9765bd33a1425113d2`
>
> **First populated reference:** verified Coinbase product record `UNI-USD`, rendered to the user as `UNI / USDC`; 2h strategy, backtest, and paper chain

## 1. Purpose

Turn the existing strategy, research, backtest, deployment, and rationale surfaces into one coherent **strategy-version workspace** for a technically comfortable single-user Coinbase spot trader.

The workspace carries one immutable evidence identity through:

1. **Define**
2. **Validate & Publish**
3. **Backtest**
4. **Paper**
5. **Decisions / Why**

This is an evidence path, not a readiness state machine. The interface must show what evidence exists, what is blocked, and what is unknown without inventing a single readiness API, a backtest-to-deployment promotion record, or an order-to-intent join.

## 2. Product posture

### Primary surface

**Command / Inspect**, with subordinate Configure and Monitor surfaces.

- **Configure:** define and validate a mutable strategy draft.
- **Inspect:** review an exact published version and its provenance.
- **Monitor / Operate:** inspect and control a paper runtime without obscuring exposure.

### User and success condition

The user understands trading and technical identifiers but should not have to mentally join five routes. Success means the user can answer, from any workspace stage:

- Which strategy version am I looking at?
- Is this draft mutable or is this version immutable?
- Which product and timeframe does it use?
- What evidence exists for this exact fingerprint?
- Which assumptions and validity limits apply to that evidence?
- Is paper running, paused, stopping, or flattening?
- What exposure and protection remain?
- Why did the latest completed bar produce a trade, no trade, or an unavailable explanation?

### Design direction

Preserve the existing dark, restrained workstation character. Change the composition before changing the palette:

- hierarchy and evidence identity replace repeated hero copy;
- lists and definition rows replace unnecessary cards;
- status language is explicit and text-first;
- fingerprints use monospace but never become the primary label;
- color reinforces meaning but never carries it alone;
- motion is limited to useful progress and removed under reduced-motion preferences.

## 3. Contract truths and non-goals

### Preserve these truths

- Drafts are mutable and revision-guarded.
- Published versions are immutable and identified by `strategy_fingerprint`.
- A stable `strategy_id` may have several published versions plus one open next draft.
- Backtests identify an exact strategy, dataset, run, engine, signal trace, and result.
- A deployment identifies the exact `strategy_fingerprint` it runs.
- A deployment may expose persisted trade reasons by `deployment_id`.
- A trade reason identifies an intent and may contain a server-composed reconciliation to an order.
- The backend lifecycle distinguishes status from lifecycle instruction.
- Managed stop and flatten are both handled by the existing stop route; flatten is `POST /api/v1/deployments/{id}/stop?flatten=true`.

### Do not add or imply

- one authoritative `ready` or `not ready` backend state;
- a direct backtest-to-deployment promotion link;
- that a deployment came from a particular backtest merely because both use the same strategy fingerprint;
- an inferred reverse join from an arbitrary deployment order to an intent;
- a standalone flatten endpoint;
- that a successful flatten request means inventory is already flat;
- that `worker_lease_held` proves worker health;
- that an older 2h runtime snapshot is stale solely because of its timestamp.

### Currency and product display

The canonical product record remains exactly `UNI-USD` in APIs, fingerprints, exports, URLs, audit evidence, and forensic disclosures.

All user-facing quote and cash language in this slice is **USDC**:

- `UNI / USDC`
- `10,000.00 USDC starting cash`
- `Minimum notional (USDC)`
- `Net P&L (USDC)`

Where exact provenance matters, disclose:

> Coinbase product record: `UNI-USD`

Do not rewrite canonical identifiers inside evidence.

## 4. Workspace shell and navigation

### Desktop shell, 1024px and wider

Use three layers:

1. **Global navigation** — routes for the whole product.
2. **Workspace identity bar** — persistent exact strategy-version context.
3. **Stage navigation** — the five-step evidence path.

#### Global navigation reduction

The current eleven equal-weight links should become four labeled groups rather than one wrapping row:

- **Monitor:** Portfolio
- **Build:** Strategies, Research, Backtests
- **Operate:** Deploy, Trade
- **Review:** Decisions, Audit
- **System menu:** Chat, Memory, Settings

`Decisions` may initially route to the existing Journals/Memory destinations; do not merge contracts as part of this slice. The grouping is a presentation change, not a route or authority change.

At 1024px and wider, show the active group and its local destinations. Keep the system menu visually secondary.

#### Workspace identity bar

On all strategy-version surfaces show:

- strategy name;
- `Draft vN` or `Published vN`;
- full fingerprint access, using a shortened visible value plus **Copy full fingerprint**;
- `UNI / USDC`;
- `2h`;
- lifecycle/evidence summary;
- a clear **Change strategy/version** control.

For a draft with no fingerprint, show:

> Draft vN · fingerprint created at publication

The identity bar is sticky on desktop only while its compact form remains readable. Do not place primary mutations inside it.

#### Stage navigation

Show a horizontal ordered navigation:

`Define → Validate & Publish → Backtest → Paper → Decisions / Why`

Rules:

- The stage control communicates location and evidence availability, not completion.
- Never show a completion checkmark unless an existing record proves that evidence exists.
- A missing stage says `No evidence` or names its blocker.
- The current stage uses `aria-current="step"`.
- Stages are links only when a current contract supplies a valid destination.
- Backtest never links directly to Paper.
- Paper navigation starts from the strategy-version context, not from a selected backtest result.

### Mobile shell, below 720px

Mobile is a distinct monitoring and safe-control context, while still supporting full authoring:

- top bar contains brand, current section, and one 44×44 **Menu** button;
- the menu opens a focus-managed full-height navigation sheet;
- the workspace identity becomes a two-line summary with **Details** disclosure;
- stage navigation becomes a labeled select or disclosure list, not a squeezed horizontal stepper;
- no page-level horizontal scrolling;
- forensic tables may use a clearly labeled, keyboard-focusable horizontal scroll region;
- sticky bottom actions must not cover focused fields or OS keyboard content.

## 5. Strategy library

Route: `/strategies`

Primary surface: **Operate / Explore**.

### Desktop hierarchy

Use a dense semantic table with persistent actions:

1. **Strategy** — name, lifecycle composition
2. **Market** — `UNI / USDC · 2h`
3. **Version evidence** — latest published version and fingerprint
4. **Latest backtest** — result summary and evidence date, or `No backtest evidence`
5. **Paper** — lifecycle plus exposure summary, or `No paper deployment`
6. **Action** — persistent primary action and overflow

Lifecycle composition examples:

- `Draft v3 · 2 published versions`
- `Published v2`
- `Archived v2`

Do not reduce a strategy with an open draft and published history to one `status` pill.

Primary action:

- draft: **Continue defining**;
- published or archived: **Inspect version**.

Overflow actions:

- Research this version
- Version history
- Clone into new strategy
- Archive…

Remove the hover-only toolbar and non-focusable clickable row. Every action must remain visible or reachable by keyboard and touch.

### New strategy

Replace the current button-plus-adjacent hidden defaults with **New strategy…**. The dialog contains:

- Template
- Timeframe
- Product disclosure (`UNI / USDC`; canonical product in help text)
- **Cancel**
- **Create draft**

Creation is a mutation and requires confirmation within this dialog. Default focus is Cancel; submission uses the existing create contract.

### Mobile cards

Below 720px each strategy becomes a semantic `<article>` containing:

- name and lifecycle;
- `UNI / USDC · 2h`;
- latest backtest evidence or explicit absence;
- paper state and exposure or explicit absence;
- a full-width 44px primary action;
- a 44×44 labeled overflow action.

## 6. Define: strategy builder

Route: `/strategies/[id]`

### Desktop layout

Use a three-column working layout:

- **Left, 220–248px:** vertical section navigation with validation counts.
- **Center:** active form section.
- **Right, 320–360px:** sticky review inspector.

Replace the eight wrapped pills with vertical navigation. Each item shows a textual state:

- `Complete`
- `2 issues`
- `Not reviewed`

These are client-derived form-validation summaries, not backend readiness.

### Form hierarchy

Always visible at top:

- strategy name;
- `Draft vN`;
- `UNI / USDC · 2h`;
- saved/unsaved state;
- Save draft…;
- Validate & publish…

Sections:

1. Overview
2. Market and data
3. Indicators
4. Entry conditions
5. Exit conditions and protection
6. Position sizing
7. Portfolio limits
8. Execution preferences

Keep the default path concise. Move advanced controls behind disclosures only when hiding them does not hide current risk:

- **Advanced indicator settings** — per-indicator alternate timeframe and uncommon parameters.
- **Higher-timeframe filter** — disclosed by its enable control; once enabled, fields stay visible.
- **Advanced execution preferences** — declared-but-unsupported runtime preferences.
- **Engine support** — expanded by default on desktop, collapsed by default on mobile.

Never hide:

- product/timeframe;
- side;
- entry logic;
- initial stop;
- take profit;
- time exit;
- risk fraction;
- maximum notional;
- maximum strategy exposure;
- validation problems;
- unsupported semantics that materially limit the backtest or runtime.

### Review inspector

Order:

1. Plain-English summary
2. Validation issues
3. Required data
4. Save state
5. Engine support and validity limits

Validation issues link to and focus their exact fields. A disabled Save or Publish action must expose the reason in adjacent text and through `aria-describedby`.

### Mobile transformation

- replace vertical navigation with a `Section` select or accordion;
- stack all fields;
- keep comparison operands in logical reading order;
- prevent nested rule indentation from consuming the viewport—use a vertical rule rail and depth labels instead of growing left margin indefinitely;
- place a collapsible **Review and validation** summary immediately before publication;
- use a bottom action bar for **Save draft…** and **Validate & publish…** only when it does not obscure focused controls.

## 7. Validate & Publish

Publication creates the immutable evidence boundary and needs a dedicated review surface before mutation.

### Review panel

Show:

- strategy name;
- next version number;
- `UNI / USDC · 2h`;
- plain-English definition;
- validation result;
- required data;
- engine support and known unsupported declarations;
- semantic diff from the prior published version when one exists;
- notice that the name and definition contribute to immutable identity.

There is no single readiness API. The panel is assembled from existing draft validation, source, and version-history data. Label missing data as `Unavailable`, not `Passed`.

### Publication confirmation dialog

**Title**

> Publish immutable strategy version?

**Context**

> `{Strategy name}` · Version `{N}`  
> `UNI / USDC` · `2h`

**Body**

> Publishing creates an immutable fingerprint used by research and runtimes. This version cannot be edited. Further changes require a new draft version.

**Evidence preview**

- Validation: `No blocking definition errors` or exact blockers
- Canonical product record: `UNI-USD`
- Expected fingerprint: `Created after publication`

**Actions**

- Secondary: **Cancel**
- Primary: **Publish version**
- Pending: **Publishing…**

Default focus is Cancel. Escape closes before submission. Once submitted, Escape does not pretend to cancel the request.

### Success state

Show:

- `Published vN`;
- full copyable fingerprint;
- **View published version**;
- **Set up a backtest**;
- **Back to strategies**.

Do not show a direct Paper/Deploy call to action in the publication success state.

## 8. Backtest

Routes: `/research` for configuration and submission; `/backtests` for immutable result inspection.

### Launch hierarchy

Always visible:

- exact strategy name, version, and fingerprint;
- `UNI / USDC · 2h`;
- dataset identity and verified coverage;
- date window;
- initial capital in USDC;
- engine contract;
- maker/taker fees;
- submit confirmation.

Advanced disclosure:

- spread/latency/fill settings when supported;
- study configuration;
- reproducibility fields that do not affect the default task.

Do not bury trust-critical modeled assumptions. On result detail, assumptions and validity limits remain visible above performance charts.

### Result list

Resolve `strategy_fingerprint` client-side against strategy histories when possible. Lead with:

- strategy name and version;
- `UNI / USDC · 2h`;
- net return;
- maximum drawdown;
- trade count;
- engine;
- result publication time.

Show the shortened result fingerprint as secondary evidence. If strategy metadata cannot be resolved, show the fingerprint without inventing a name or version.

### Result detail hierarchy

1. Immutable result identity
2. Core metrics
3. Validity limits and modeled assumptions
4. Buy-and-hold comparison
5. Equity curve
6. Modeled trade ledger
7. Forensic provenance

Do not label a result `passed`, `ready`, or `deployable`.

### Zero trades

Distinguish:

- `No qualifying trades were modeled in this historical window.`
- missing trade ledger or result detail as an unavailable/error state.

A zero-trade backtest does not prove runtime conditions recently failed.

## 9. Paper runtime

Routes: `/deploy?strategy=<strategy_id>` and `DeployWorkstation.svelte`.

The first slice defaults to **Paper**. Live remains visibly distinct and requires its existing stronger acknowledgement, but live redesign is not the validation target for this slice.

### Deployment selection

Filter runtime rows by exact `strategy_fingerprint`, not only `strategy_id`, when the workspace is anchored to a published version. Other versions may be discoverable through **Other deployments for this strategy**, but they must not be mixed into the current version's evidence.

### Start-paper panel

Always show:

- exact published version and fingerprint;
- `UNI / USDC · 2h`;
- starting cash in USDC;
- maker and taker fee assumptions;
- higher-timeframe and extra-timeframe data requirements;
- a statement that this starts a new deployment of the version, not a promotion of a selected backtest.

Start is a mutation and requires confirmation.

### Runtime summary

Separate these concepts:

- **Lifecycle:** Running / Paused / Stopped
- **Instruction:** Entries enabled / Stop new entries / Managed stop / Flatten requested
- **Entry eligibility:** Eligible / Blocked by pause / Blocked by daily-loss breaker / Blocked by drawdown breaker / Unavailable
- **Exposure:** open books, quantity, entry price, current protective stop/target
- **Working orders:** count and purpose when available
- **Cash:** ledger cash and capital summary in USDC
- **Last evaluation:** signal plus completed-bar time
- **Snapshot:** server timestamp plus freshness/availability state

Required frontend fields include `lifecycle_command`, `daily_loss_latched`, `drawdown_latched`, `revision`, and `worker_lease_held`. Missing or unknown lifecycle fields fail closed: show readable inventory where possible, but disable lifecycle mutations.

Derived examples:

- `running + none + no latches` → **Running · entries eligible**
- `running + latch` → **Running · entries blocked by breaker**
- `paused + stop_new_entries` → **Paused · protection continues**
- `stopped + managed_shutdown + residual exposure` → **Stopped · managing residual exposure**
- `stopped + flatten + residual exposure` → **Stopped · flatten requested**
- `stopped + flatten + no positions + no working orders` → **Stopped · no remaining inventory or working orders in this snapshot**
- unknown combination → **Lifecycle state unavailable**; actions disabled

`Eligible` means only that this lifecycle/breaker layer permits entries. It does not promise an order; data, sizing, connection, and other risk checks still apply.

## 10. Lifecycle confirmation dialogs

All lifecycle mutations require confirmation. Use a shared accessible dialog shell with action-specific content.

### Shared anatomy

1. Clear verb-led title
2. Strategy/mode/product/timeframe identity
3. Snapshot timestamp
4. Current exposure and working-order count
5. Exact consequence
6. What does not happen
7. Asynchronous/unknown-outcome notice when relevant
8. Cancel and explicit action label

Default focus is Cancel. Focus is trapped. Escape closes only before submission. Closing restores focus to the trigger. Pending and completion are announced through a polite live region; errors use an alert.

### Pause

**Trigger:** Pause entries…

**Title**

> Pause new paper entries?

**Context**

> `{Strategy name}` · Paper · `UNI / USDC` · `2h`  
> `{open_books}` open · `{working_orders}` working · snapshot `{timestamp}`

**Body**

> New risk-increasing entries and repricing will stop. Existing positions are not flattened, and protective exits continue to be maintained.

**Actions**

- **Cancel**
- **Pause entries**
- pending: **Pausing…**

**Success**

> Deployment paused. New entries are blocked; existing protection remains active.

### Resume

**Trigger:** Resume entries…

**Title**

> Resume paper deployment?

**Body**

> New risk-increasing orders may be submitted when the next eligible completed 2h bar is processed. Existing inventory and protection are unchanged by this command.

If one or both breakers are latched, add:

> `{Daily-loss / Drawdown / Daily-loss and drawdown}` breaker is latched. Resume changes the lifecycle to Running, but new entries remain blocked until the latch is reset separately.

Do not combine breaker reset with Resume.

**Actions**

- **Cancel**
- **Resume entries**
- pending: **Resuming…**

### Stop

**Trigger:** Stop…

**Title**

> Stop paper deployment?

**Permanent warning**

> This deployment cannot be resumed after it is stopped.

**Context**

> `{Strategy name}` · Paper · `UNI / USDC` · `2h`  
> `{open_books}` open · `{working_orders}` working · snapshot `{timestamp}`

**Choice 1 — selected by default**

> **Managed stop — keep protection**  
> Stop new entries and cancel risk-increasing entry orders. Keep protective exits active. Open positions remain in account-level risk until they close.

**Choice 2**

> **Stop and flatten**  
> Stop new entries, submit marketable exits for open inventory, then cancel remaining orders. Exit price and fees are not guaranteed.

**Asynchronous note**

> The lifecycle state changes when the request is accepted. Cancellation and exits are applied asynchronously by the worker.

**Actions**

- **Cancel**
- managed selection: **Stop with protection** / **Stopping…**
- flatten selection: **Stop and flatten** / **Requesting flatten…**

Managed stop uses `POST /stop` without the flatten query flag. Explicit flatten uses `POST /stop?flatten=true`.

### Flatten after managed stop

Show **Flatten remaining exposure…** only when a fresh snapshot says:

- status is stopped;
- instruction is managed shutdown;
- positions or working orders remain.

**Title**

> Flatten stopped deployment?

**Body**

> Submit marketable exits for any remaining inventory, then cancel remaining orders. This changes the shutdown instruction from Managed stop to Flatten; it does not restart the deployment.

**Actions**

- **Cancel**
- **Flatten remaining exposure**
- pending: **Requesting flatten…**

### Completion language

Mutation responses acknowledge an instruction; they do not prove settlement.

- Managed stop accepted: `Managed stop accepted. Protection remains active while residual exposure settles.`
- Flatten accepted: `Flatten request accepted. Verifying remaining inventory and orders…`
- Flatten complete only after a fresh snapshot reports zero positions, zero open books, and zero working orders.

## 11. Decisions / Why

Anchor this surface to the same immutable triplet:

`strategy_id + version + strategy_fingerprint`

Then join only what current contracts prove:

1. strategy source/history by stable identity and fingerprint;
2. backtests filtered by exact `strategy_fingerprint`;
3. deployments filtered by exact `strategy_fingerprint`;
4. trade reasons filtered by selected `deployment_id`;
5. orders/fills from the selected deployment snapshot;
6. a reason to an order only when `reason.reconcile.order_id === order.id`.

Use precise labels:

- **Backtests for this version**
- **Paper deployments using this version**
- **Same strategy version**
- **Why this intent was persisted**

Never say `Deployed from this backtest` or infer an intent for an arbitrary order.

### Decision timeline

For the selected paper deployment, show newest first:

- completed-bar evaluation;
- signal outcome;
- risk decision when an intent exists;
- intent identity;
- reconciled order/fills when supplied by the reason payload;
- operator notes.

### Required distinctions

#### Conditions did not match

Source: `last_signal === "not_matched"` plus `last_evaluated_bar`.

> **No trade — conditions did not match**  
> The completed 2h bar at `{UTC}` was evaluated and did not satisfy the entry rules.

This is a successful neutral evaluation, not an error. No intent, order, or rationale record is expected.

#### Conditions could not be evaluated

Source: `last_signal === "undefined"`.

> **No trade — conditions could not be evaluated**  
> Review available coverage or runtime mismatch details.

#### No reasons returned

> **No recorded trade rationale for this deployment**  
> This can mean no intent was persisted, or rationale recording was unavailable. Orders cannot be matched to intents from the current deployment response.

Do not convert this into `Conditions did not match` without runtime signal evidence.

#### Known intent rationale unavailable

When a known intent detail returns 404:

> **Rationale record unavailable for intent `{id}`**

This is the defensible missing-rationale state.

#### Reason exists, no reconciled order

- ledger available and `order_status === null`: **Intent recorded; no venue-visible order found.**
- ledger unavailable: **Execution ledger unavailable; order outcome cannot be reconciled.**
- empty notes: **No operator note.** The frozen signal and risk decision remain visible.

## 12. Provenance navigation

Expose the complete identity even when the visible label is abbreviated.

| Evidence | Current-contract navigation |
| --- | --- |
| Result fingerprint | Self-link to `/backtests?result=<result_fingerprint>` and Copy |
| Strategy fingerprint | Resolve through strategy history; open exact version inspector and Copy |
| Exact version research | `/research?strategy=<strategy_id>&strategy_fingerprint=<fingerprint>` |
| Dataset fingerprint | Filter results using that dataset when supported; label `Results using this dataset`, not `Dataset details` |
| Run fingerprint | Filter results using that run when supported |
| Signal trace fingerprint | Copy only; no detail route exists |
| Engine contract | Text plus engine-support disclosure |
| Paper deployment | `/deploy?strategy=<strategy_id>` with exact version selected in workspace state |
| Trade reason | Existing intent-specific detail when an intent ID is supplied |

Only one backtest source filter may be active at a time, matching the backend contract. Unresolvable metadata falls back to canonical identifiers.

The version inspector should support a query or fragment that selects the exact fingerprint. If this requires a frontend-only route-state addition, it must not change strategy contracts.

## 13. Progressive disclosure rules

### Level 1 — scan

Always visible:

- strategy name/version;
- human market and timeframe;
- immutable/mutable status;
- evidence availability;
- backtest return/drawdown/trades;
- paper lifecycle/exposure;
- primary action.

### Level 2 — inspect

Visible within a stage:

- plain-English definition;
- validation;
- benchmark;
- equity curve;
- modeled assumptions;
- current protection;
- version history;
- decision timeline.

### Level 3 — forensic evidence

Use semantic disclosures for:

- full fingerprints;
- run and signal-trace identity;
- canonical Coinbase product record;
- semantic version diff;
- engine support matrix;
- export definition;
- complete modeled trade ledger on narrow screens;
- full orders/fills history.

Trust-critical assumptions, remaining exposure, and destructive consequences are never hidden under `Advanced`.

## 14. State model

Every stage must implement these states explicitly.

### Loading

- preserve stable page structure;
- skeleton only the regions being loaded;
- mark status with `aria-busy` and announce useful progress;
- no enabled placeholder actions before required contract fields are known.

### Empty

Describe what is absent and the valid next action:

- `No published version yet — validate and publish this draft first.`
- `No backtest evidence for this version.`
- `No paper deployment using this version.`
- `No recorded trade rationale for this deployment.`

Empty and error must never render simultaneously.

### Error

Place the error beside the affected region, preserve unaffected evidence, and offer a specific Retry. Do not collapse all failures into a page-wide unavailable state.

### Partial

Show available evidence and name what is missing:

> Backtest result loaded; benchmark comparison is unavailable.

> Strategy version loaded; paper runtime could not be refreshed.

### Stale

After a refresh failure, preserve the last successful snapshot, visibly mark it `Stale`, show its timestamp, and disable mutations until a current snapshot is loaded.

Do not infer staleness merely from an old `updated_at` on a quiet 2h deployment.

### Unavailable or incompatible contract

If required lifecycle fields are absent or malformed:

> Lifecycle controls are unavailable because this server did not return the current lifecycle contract.

Show safely parseable evidence read-only. Do not default missing fields to safe-looking values.

### Mutation pending

- lock only the affected object;
- keep its prior snapshot visible;
- show a verb-specific pending label;
- prevent duplicate submission;
- never auto-retry lifecycle mutations.

### Mutation success, refresh failure

Merge the mutation response first, then refresh.

> Action accepted at `{time}`, but the latest reconciliation is unavailable. Showing response revision `{revision}`. Retry refresh before taking another action.

### Ambiguous network interruption

> Outcome unknown. Refresh this deployment before retrying the action.

### Conflict and not found

- 409 revision conflict: `This deployment changed before the action was applied. Refresh and review its latest state.`
- resume after stopped: `This deployment is stopped and cannot be resumed.`
- 404: `This deployment is no longer available. Refresh the deployment list.`
- 503/offline: preserve last snapshot, mark stale, disable mutations, and provide Retry.

## 15. Accessibility and interaction requirements

### Targets and focus

- all pointer/touch targets are at least 44×44 CSS pixels;
- every action works without hover;
- every interactive element has a visible 2px minimum `:focus-visible` indicator;
- focus order follows visual and task order;
- route changes place focus on the new page heading unless an intentional in-place update preserves it.

### Dialogs and sheets

- use `role="dialog"`, `aria-modal="true"`, labeled title, and described body;
- move focus to Cancel by default for irreversible or risk-affecting actions;
- trap focus;
- Escape closes before submission;
- background is inert;
- closing restores focus;
- narrow mobile dialogs become full-screen sheets.

### Navigation semantics

- do not mix links and ARIA tabs inside one `tablist`;
- use ordinary navigation links for route changes;
- use tabs only for same-page panels and implement arrow keys, `aria-controls`, selected state, and roving tab index;
- builder section navigation uses `aria-current` or `aria-pressed` consistently.

### Tables and overflow

- every table has an accessible name or caption;
- column headers use `scope="col"`;
- horizontal scroll regions are keyboard focusable and include an accessible overflow cue;
- mobile discovery lists transform into articles instead of forcing horizontal scrolling.

### Async and validation

- loading, success, stale, and partial-success updates use live regions without stealing focus;
- errors use alerts only when immediate interruption is warranted;
- field errors are associated with fields;
- the validation summary links to each invalid field;
- disabled actions explain why in visible text and accessible descriptions.

### Contrast

- normal text meets WCAG AA 4.5:1;
- large text and non-text indicators meet 3:1;
- do not use the current low-contrast `#657174` for 10–12px table labels against the dark surface;
- status always includes text or shape, never color alone.

### Reduced motion

Under `prefers-reduced-motion: reduce`:

- remove spinner rotation and skeleton shimmer;
- remove nonessential transitions;
- keep progress understandable through static text.

## 16. Responsive acceptance matrix

Verify at 1440, 1024, 768, 390, and 320 CSS pixels.

### All widths

- no page-level horizontal overflow;
- identity, mode, product, timeframe, and exposure remain understandable;
- no control is clipped at 200% zoom;
- visible focus is not covered by sticky UI;
- long names and full fingerprints wrap or disclose without breaking layout.

### 720px and below

- global navigation uses a menu sheet;
- strategy and backtest discovery use articles/cards;
- builder uses section select/accordion;
- inspector becomes a disclosure;
- dialogs use full-screen sheets;
- dense ledgers use labeled focused overflow or a stacked inspection view;
- stage navigation does not horizontally scroll.

## 17. Testable acceptance criteria

### Evidence chain

1. A user can create, edit, save, validate, and publish a draft through existing contracts.
2. The selected strategy name, exact version, fingerprint, product, and timeframe persist across all workspace stages.
3. An open draft and earlier published versions are represented simultaneously when both exist.
4. Publication requires explicit confirmation and produces an immutable fingerprint display.
5. Backtest setup always names an exact published fingerprint and exact dataset identity.
6. Backtest result detail exposes Result, Run, Strategy, Dataset, Signal trace, and Engine identities.
7. Copy actions copy complete canonical identifiers.
8. Backtest screens contain no direct Deploy or Start paper promotion action.
9. Paper setup starts from strategy-version context and does not claim provenance from a selected backtest.
10. No authoritative readiness API, aggregate readiness badge, or inferred order-to-intent relation is added.

### Product and currency

11. `UNI-USD` remains unchanged in canonical evidence, APIs, exports, and copyable provenance.
12. Human market labels use `UNI / USDC`.
13. User-facing quote, notional, P&L, and starting-cash labels use USDC.
14. The canonical Coinbase record is disclosed in forensic evidence.

### Runtime lifecycle

15. The frontend models status, lifecycle instruction, both breaker latches, revision, and lease evidence.
16. Unknown or absent lifecycle values disable mutations.
17. Pause calls exactly `POST /pause` and says protection continues.
18. Resume calls exactly `POST /resume` and does not imply a breaker reset.
19. Managed stop calls exactly `POST /stop` without `flatten=true`.
20. Explicit flatten calls exactly `POST /stop?flatten=true`.
21. Managed stop is described as permanent, protective, and potentially still risk-bearing.
22. Flatten is described as marketable and asynchronous with no guaranteed exit price.
23. A stopped managed deployment with residual exposure can request flatten.
24. A stopped deployment never exposes Resume.
25. Flatten remains `requested` until a fresh zero-position, zero-open-book, zero-working-order snapshot arrives.
26. A mutation response is merged before a reconciliation refresh.
27. Mutation success plus refresh failure renders partial success rather than failure.
28. Network interruption renders unknown outcome and is not automatically retried.
29. Switching strategy/version clears prior runtime rows before loading the new selection.

### Decisions / Why

30. Backtests and deployments are filtered by exact strategy fingerprint.
31. Trade reasons are filtered by selected deployment ID.
32. A reason links to an order only when the server-composed reason supplies the matching order ID.
33. `not_matched` renders `No trade — conditions did not match` with the completed-bar time.
34. `undefined` renders a distinct could-not-evaluate state.
35. No reasons returned does not claim that conditions failed.
36. A known missing intent reason names the unavailable intent ID.
37. Empty notes say `No operator note` while preserving frozen signal and risk evidence.

### Accessibility

38. Every action is keyboard- and touch-operable without hover.
39. Every target is at least 44×44 CSS pixels.
40. Dialogs provide focus entry, trap, Escape behavior, inert background, and focus restoration.
41. Mixed link/tab semantics are removed.
42. Validation errors link to their controls.
43. Async loading and mutation outcomes are announced.
44. All tables have accessible names and scoped headers.
45. Normal text meets 4.5:1 contrast.
46. Reduced-motion users receive no spinner rotation or shimmer.

### Automated and manual verification

47. Unit tests assert exact managed-stop and flatten URLs.
48. Component tests cover supported lifecycle/status/latch combinations and unknown combinations.
49. Paper 2h end-to-end coverage includes start, pause, resume, managed stop, stop-and-flatten, and managed-stop-to-flatten.
50. State tests cover loading, empty, error, partial, stale, offline/503, 404, 409, malformed payload, ambiguous mutation outcome, and success-plus-refresh-failure.
51. Narrow mobile tests run at 390px and 320px.
52. Keyboard tests cover all menus, stage navigation, builder sections, disclosures, and dialogs.
53. Manual browser verification covers 1440, 1024, 768, 390, and 320 widths, 200% zoom, reduced motion, and visible focus.

## 18. Recommended implementation order

1. Add the shared workspace identity and stage-navigation model without changing backend contracts.
2. Remove hover-only library actions and implement responsive list/card transformations.
3. Recompose the builder and publication confirmation around exact version identity.
4. Expand backtest provenance and assumption hierarchy; add safe exact-version navigation.
5. Correct deployment typing and lifecycle semantics before visual restructuring.
6. Implement pause, resume, managed-stop, and flatten confirmations with mutation/reconciliation state handling.
7. Add exact-fingerprint deployment filtering and Decisions / Why distinctions.
8. Apply shared focus, target-size, contrast, live-region, and reduced-motion requirements.
9. Add the acceptance tests and verify the populated `UNI-USD` / displayed `UNI / USDC`, 2h paper chain.

## 19. Known implementation risks

- The current browser deployment type drops lifecycle fields required for safe control; lifecycle UI must fail closed until typing and parsing are corrected.
- The current strategy library's hover toolbar is not salvageable for touch/keyboard; it needs recomposition, not cosmetic fixes.
- The current dialog implementations do not manage focus or Escape and should be replaced by a shared dialog primitive.
- Exact strategy-version selection may need frontend route/query state, but it must not change immutable strategy contracts.
- Backtest list filters support only one source filter at a time; the UI must not compose unsupported filters.
- Orders cannot be reverse-joined to intents from deployment payloads. Keep rationale navigation driven by trade-reason records only.
- A realistic seeded state set is required to verify healthy, stale, partial, breaker-latched, managed-stopping, flatten-requested, no-trade, and missing-rationale states without using live trading.
