<script lang="ts">
	/**
	 * The expanded body of one decision row: bar facts, the evaluated entry rule
	 * (with the higher-timeframe filter and indicator values), the exit rule, the
	 * risk verdict, order action with linked orders and fills, the position
	 * snapshot, and the persisted trade reason this row owns (or where it is shown).
	 */
	import {
		barSpanText,
		conditionChipText,
		conditionChipTitle,
		conditionGroupDescription,
		conditionGroupText,
		conditionResultLabel,
		decisionActionLabel,
		decisionIntentIds,
		exitReasonLabel,
		formatDecimalCompact,
		htfFilterChipText,
		intentPurposeLabel,
		positionSnapshotText,
		riskVerdictText,
		ruleOutcomeLabel,
		ruleOutcomeResult,
		skipReasonLabel,
		type BarDecision,
		type ConditionNode,
		type TradeReasonAssignment
	} from '$lib/decisions';
	import { shortStrategyFingerprint } from '$lib/strategy-workspace';
	import { formatUtcTimestamp } from '$lib/time';
	import type { TradeReasonState } from '$lib/trade-reasons';
	import DecisionTradeReason from './DecisionTradeReason.svelte';

	let {
		decision,
		key,
		idBase,
		assignment,
		rowByKey,
		reasons
	}: {
		decision: BarDecision;
		/** This row's `decisionKey`. */
		key: string;
		/** Prefix for this row's heading ids. */
		idBase: string;
		/** Trade reasons joined onto the loaded rows. */
		assignment: TradeReasonAssignment;
		/** Loaded rows by `decisionKey`, to name the row that shows a shared intent's reason. */
		rowByKey: Map<string, BarDecision>;
		reasons: TradeReasonState | undefined;
	} = $props();

	const rule = $derived(decision.rule);
	const owned = $derived(assignment.byDecision.get(key) ?? []);
	const intents = $derived(decisionIntentIds(decision));
</script>

{#snippet conditionTree(node: ConditionNode)}
	{#if node.node === 'comparison'}
		<span
			class="cond {node.result}"
			data-testid="condition-chip"
			data-result={node.result}
			title={conditionChipTitle(node)}
			>{conditionChipText(node)}<span class="sr-only"
				>{` (${conditionResultLabel(node.result)})`}</span
			></span
		>
	{:else}
		<div
			class="group {node.result}"
			data-testid="condition-group"
			data-group={node.node}
			data-result={node.result}
		>
			<span class="group-label" title={conditionGroupDescription(node)}
				>{conditionGroupText(node)}<span class="sr-only">: {conditionGroupDescription(node)}</span
				></span
			>
			<div class="group-children">
				{#each node.children as child, index (index)}
					{@render conditionTree(child)}
				{/each}
			</div>
		</div>
	{/if}
{/snippet}

<dl class="kv">
	<div>
		<dt>Bar</dt>
		<dd>{barSpanText(decision)}</dd>
	</div>
	<div>
		<dt>Evaluated</dt>
		<dd>{formatUtcTimestamp(decision.evaluated_at)}</dd>
	</div>
	<div>
		<dt>Close</dt>
		<dd title={decision.close_price ?? undefined}>
			{formatDecimalCompact(decision.close_price)}
		</dd>
	</div>
	<div>
		<dt>Reason code</dt>
		<dd class="mono">{decision.reason_code}</dd>
	</div>
	{#if decision.skip_reason !== null}
		<div>
			<dt>Skipped because</dt>
			<dd>{skipReasonLabel(decision.skip_reason)}</dd>
		</div>
	{/if}
	{#if decision.exit_reason !== null}
		<div>
			<dt>Exit reason</dt>
			<dd>{exitReasonLabel(decision.exit_reason)}</dd>
		</div>
	{/if}
	<div>
		<dt>Book</dt>
		<dd>{decision.mode === 'live' ? 'LIVE' : 'Paper'} · {decision.product_id}</dd>
	</div>
	{#if decision.strategy_fingerprint !== null}
		<div>
			<dt>Rules snapshot</dt>
			<dd class="mono" title={decision.strategy_fingerprint}>
				{shortStrategyFingerprint(decision.strategy_fingerprint)}
			</dd>
		</div>
	{/if}
</dl>

<section class="block" aria-labelledby="{idBase}-rule">
	<h3 id="{idBase}-rule">Entry rule</h3>
	{#if rule === null}
		<p class="quiet">
			Entry rules were not evaluated on this bar{decision.skip_reason === null
				? ''
				: ` (${skipReasonLabel(decision.skip_reason)})`}.
		</p>
	{:else}
		<p class="verdict" data-testid="decision-rule-outcome" data-outcome={rule.outcome}>
			Entry rule {ruleOutcomeLabel(rule.outcome)}{rule.htf_filter === null
				? ''
				: ' (entry conditions and the higher-timeframe filter combined)'}
		</p>
		<div class="tree">{@render conditionTree(rule.entry)}</div>
		{#if rule.htf_filter !== null}
			{@const htf = rule.htf_filter}
			<div class="htf">
				<span
					class="cond htf-chip {ruleOutcomeResult(htf.outcome)}"
					data-testid="htf-chip"
					data-outcome={htf.outcome}>{htfFilterChipText(htf)}</span
				>
				<div class="tree">{@render conditionTree(htf.condition)}</div>
			</div>
		{/if}
		{#if rule.signal !== null}
			{@const signal = rule.signal}
			<h4>
				Indicator values · bar starting {formatUtcTimestamp(signal.candle_starts_at).slice(0, 16)} UTC
			</h4>
			{#if signal.indicator_values.length === 0}
				<p class="quiet">No indicator values were recorded for this bar.</p>
			{:else}
				<dl class="kv values" data-testid="decision-indicators">
					{#each signal.indicator_values as item (item.indicator_id)}
						<div>
							<dt class="mono">{item.indicator_id}</dt>
							<dd title={item.value ?? 'undefined (for example during warmup)'}>
								{item.value === null ? 'n/a' : formatDecimalCompact(item.value)}
							</dd>
						</div>
					{/each}
				</dl>
			{/if}
		{/if}
	{/if}
</section>

{#if decision.exit_rule}
	{@const exitRule = decision.exit_rule}
	<section class="block" aria-labelledby="{idBase}-exit-rule" data-testid="decision-exit-rule">
		<h3 id="{idBase}-exit-rule">Exit rule</h3>
		<p class="verdict" data-testid="decision-exit-rule-outcome" data-outcome={exitRule.outcome}>
			Exit rule {ruleOutcomeLabel(exitRule.outcome)}{exitRule.outcome === 'matched'
				? ' (sells at this close unless the protective stop already closed the book)'
				: ''}
		</p>
		<div class="tree">{@render conditionTree(exitRule.condition)}</div>
	</section>
{/if}

<section class="block" aria-labelledby="{idBase}-risk">
	<h3 id="{idBase}-risk">Risk</h3>
	{#if decision.risk === null}
		<p class="quiet">No risk check was recorded for this bar.</p>
	{:else}
		<p
			class="risk"
			class:deny={decision.risk.decision === 'deny'}
			data-testid="decision-risk"
			data-decision={decision.risk.decision}
		>
			{riskVerdictText(decision.risk)}
		</p>
	{/if}
</section>

<section class="block" aria-labelledby="{idBase}-action" data-testid="decision-action">
	<h3 id="{idBase}-action">Action</h3>
	<p>
		{decisionActionLabel(decision.action)}{decision.intent_id === null
			? ''
			: ` · intent ${decision.intent_id.slice(0, 8)}`}
	</p>
	{#if decision.protection_update}
		<section class="block" aria-label="Protection update">
			<h3>
				Protection {decision.protection_update.kind === 'replacement' ? 'replacement' : 'canceled'}
			</h3>
			<p>
				Stop: {decision.protection_update.previous_stop_price ?? 'unknown'} → {decision
					.protection_update.stop_price ?? 'none'}. Target: {decision.protection_update
					.target_price ?? 'none'}.
			</p>
			<p>
				Confirmed open coverage: {decision.protection_update.coverage_quantity} of {decision
					.protection_update.position_quantity}.
				{decision.protection_update.fully_covered
					? 'Quantity covered.'
					: 'Coverage is incomplete or unconfirmed.'}
			</p>
			<p>Canceled: {decision.protection_update.canceled_order_ids.join(', ')}.</p>
			<p>Current: {decision.protection_update.active_order_ids.join(', ') || 'none'}.</p>
		</section>
	{/if}
	{#if decision.orders.length > 0}
		<!-- svelte-ignore a11y_no_noninteractive_tabindex -->
		<div class="table-scroll" tabindex="0" role="region" aria-label="Orders linked to this bar">
			<table>
				<caption class="sr-only">Orders linked to this decision</caption>
				<thead>
					<tr>
						<th scope="col">Purpose</th>
						<th scope="col">Side</th>
						<th scope="col">Type</th>
						<th scope="col" class="num">Size</th>
						<th scope="col" class="num">Price</th>
						<th scope="col" class="num">Filled</th>
						<th scope="col">Status</th>
					</tr>
				</thead>
				<tbody>
					{#each decision.orders as order (order.order_id)}
						<tr data-testid="decision-order" data-order-id={order.order_id}>
							<td>{intentPurposeLabel(order.purpose)}</td>
							<td>{order.side}</td>
							<td class="muted">{order.kind}</td>
							<td class="num">{order.quantity}</td>
							<td class="num">{order.price ?? 'market'}</td>
							<td class="num">{order.filled_quantity}</td>
							<td>{order.status}</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	{:else if decision.order_ids.length > 0}
		<p class="quiet">
			Linked orders: {decision.order_ids.map((orderId) => orderId.slice(0, 8)).join(', ')}
		</p>
	{/if}
	{#if decision.fills.length > 0}
		<!-- svelte-ignore a11y_no_noninteractive_tabindex -->
		<div class="table-scroll" tabindex="0" role="region" aria-label="Fills linked to this bar">
			<table>
				<caption class="sr-only">Fills linked to this decision</caption>
				<thead>
					<tr>
						<th scope="col">Filled (UTC)</th>
						<th scope="col">Side</th>
						<th scope="col" class="num">Size</th>
						<th scope="col" class="num">Price</th>
						<th scope="col" class="num">Fee</th>
					</tr>
				</thead>
				<tbody>
					{#each decision.fills as fill (fill.fill_id)}
						<tr data-testid="decision-fill">
							<td class="mono muted">{formatUtcTimestamp(fill.filled_at).slice(5, 19)}</td>
							<td>
								{fill.side ?? '—'}{fill.purpose === null
									? ''
									: ` · ${intentPurposeLabel(fill.purpose)}`}
							</td>
							<td class="num">{fill.quantity}</td>
							<td class="num">{fill.price}</td>
							<td class="num">{fill.fee}</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	{/if}
</section>

{#if decision.position !== null}
	<section class="block" aria-labelledby="{idBase}-position">
		<h3 id="{idBase}-position">Position</h3>
		<p data-testid="decision-position">{positionSnapshotText(decision.position)}</p>
	</section>
{/if}

{#if intents.length > 0}
	<section class="block" aria-labelledby="{idBase}-reason">
		<h3 id="{idBase}-reason">Trade reason</h3>
		{#each owned as reason (reason.id)}
			<DecisionTradeReason {reason} />
		{/each}
		{#each intents as intentId (intentId)}
			{@const owner = assignment.ownerByIntent.get(intentId)}
			{@const ownerRow = owner === undefined || owner === key ? undefined : rowByKey.get(owner)}
			{#if ownerRow !== undefined}
				<p class="quiet">
					The trade reason for intent {intentId.slice(0, 8)} is shown on the bar that closed
					{formatUtcTimestamp(ownerRow.bar_closes_at).slice(5, 16)} UTC.
				</p>
			{/if}
		{/each}
		{#if decision.intent_id !== null && !assignment.ownerByIntent.has(decision.intent_id)}
			{#if reasons === undefined || reasons.status === 'loading'}
				<p class="quiet">Loading the persisted trade reason…</p>
			{:else if reasons.status === 'error'}
				<p class="problem">Trade reason unavailable ({reasons.message}).</p>
			{:else}
				<p class="quiet">
					No persisted trade reason for intent {decision.intent_id.slice(0, 8)}.
				</p>
			{/if}
		{/if}
	</section>
{/if}

<style>
	.kv {
		display: flex;
		flex-wrap: wrap;
		gap: 8px 24px;
		margin: 0;
	}
	.kv dt {
		color: var(--faint);
		font-size: var(--fs-xs);
	}
	.kv dd {
		margin: 2px 0 0;
		font-size: var(--fs-sm);
	}
	.kv.values dd {
		font-family: var(--font-mono);
	}
	.block {
		display: grid;
		gap: 6px;
		min-width: 0;
	}
	.block p {
		margin: 0;
	}
	h3,
	h4 {
		margin: 0;
		color: var(--muted);
		font-size: var(--fs-xs);
		font-weight: 600;
		letter-spacing: 0.06em;
		text-transform: uppercase;
	}
	h4 {
		margin-top: 4px;
		font-weight: 500;
		letter-spacing: 0.03em;
		text-transform: none;
	}
	.verdict {
		font-weight: 500;
	}
	.tree {
		display: flex;
		flex-wrap: wrap;
		gap: 6px;
		min-width: 0;
	}
	.cond {
		display: inline-flex;
		align-items: center;
		max-width: 100%;
		min-height: 22px;
		padding: 2px 8px;
		border: 1px solid var(--line-2);
		border-radius: var(--radius-pill);
		background: var(--surface);
		color: var(--muted);
		font-family: var(--font-mono);
		font-size: var(--fs-xs);
		overflow-wrap: anywhere;
	}
	.cond.true {
		border-color: var(--accent-line);
		background: var(--accent-soft);
		color: var(--pos);
	}
	.cond.false {
		border-color: var(--danger-line);
		background: var(--danger-soft);
		color: var(--neg);
	}
	.cond.unknown {
		border-style: dashed;
		color: var(--muted);
	}
	.group {
		display: grid;
		gap: 6px;
		min-width: 0;
		padding: 4px 0 4px 10px;
		border-left: 2px solid var(--line-2);
	}
	.group.true {
		border-left-color: var(--accent-line);
	}
	.group.false {
		border-left-color: var(--danger-line);
	}
	.group-label {
		color: var(--muted);
		font-size: var(--fs-xs);
		font-weight: 600;
		letter-spacing: 0.06em;
	}
	.group-children {
		display: flex;
		flex-wrap: wrap;
		align-items: flex-start;
		gap: 6px;
		min-width: 0;
	}
	.htf {
		display: grid;
		gap: 6px;
		justify-items: start;
	}
	.htf-chip {
		font-family: var(--font-sans);
		font-weight: 500;
	}
	.risk.deny {
		color: var(--warn);
	}
	.table-scroll {
		overflow-x: auto;
		border: 1px solid var(--line);
		border-radius: var(--radius-md);
		background: var(--surface);
	}
	table {
		width: 100%;
		border-collapse: collapse;
	}
	th,
	td {
		padding: 6px 10px;
		font-size: var(--fs-sm);
		text-align: left;
	}
	th {
		color: var(--faint);
		font-weight: 500;
	}
	td {
		border-top: 1px solid var(--line);
	}
	th.num,
	td.num {
		text-align: right;
	}
	.mono {
		font-family: var(--font-mono);
	}
	.quiet,
	.muted {
		color: var(--muted);
	}
	.problem {
		color: var(--neg);
	}
</style>
