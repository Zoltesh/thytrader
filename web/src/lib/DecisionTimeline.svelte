<script lang="ts" module>
	/** Which journal to read: one bot, or every bot of a strategy (optionally one). */
	export type DecisionTimelineSource =
		| { kind: 'deployment'; deploymentId: string }
		| { kind: 'strategy'; strategyId: string; deploymentId: string | null };
</script>

<script lang="ts">
	/**
	 * Durable per-bar decision timeline, newest first
	 * (`GET /api/v1/deployments/{id}/decisions` or
	 * `GET /api/v1/strategies/{id}/decisions`). Shared by bot detail and the
	 * Why stage.
	 *
	 * Each row is one journaled bar: when it closed, what the bot decided, and
	 * the server-written reason. A row expands (a disclosure button) into the
	 * evaluated rule tree with actual values, the higher-timeframe filter,
	 * indicator values, the risk verdict, order action, linked orders and fills,
	 * the position snapshot, and the persisted trade reason its intent links.
	 * Trade reasons no loaded row links are listed once, below, as earlier
	 * trade reasons. No durable storage is an honest empty state, not an error.
	 */
	import { untrack } from 'svelte';
	import Segmented from '$lib/Segmented.svelte';
	import TradeReasonTimeline from '$lib/TradeReasonTimeline.svelte';
	import {
		DECISION_FILTERS,
		DECISION_PAGE_SIZE,
		appendDecisionPage,
		assignTradeReasons,
		barSpanText,
		conditionChipText,
		conditionChipTitle,
		conditionGroupDescription,
		conditionGroupText,
		conditionResultLabel,
		decisionActionLabel,
		decisionEmptyText,
		decisionFilterOutcomes,
		decisionIntentIds,
		decisionKey,
		decisionOutcomeLabel,
		decisionOutcomeTone,
		decisionProducts,
		exitReasonLabel,
		fetchDeploymentDecisions,
		fetchStrategyDecisions,
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
		type DecisionFilter,
		type DecisionPage,
		type DecisionStorage
	} from '$lib/decisions';
	import type { TradeReasonRecord } from '$lib/memory';
	import { shortStrategyFingerprint } from '$lib/strategy-workspace';
	import { formatUtcTimestamp } from '$lib/time';
	import {
		tradeReasonKindLabel,
		tradeReasonNotesText,
		tradeReasonReconcileText,
		type TradeReasonState
	} from '$lib/trade-reasons';

	let {
		source,
		reasons = undefined,
		multiProduct = false,
		deploymentLabel = undefined,
		timeframe = null,
		discretionary = false,
		pageSize = DECISION_PAGE_SIZE
	}: {
		source: DecisionTimelineSource;
		/** Persisted trade reasons of the same bot(s), joined onto rows by `intent_id`. */
		reasons?: TradeReasonState;
		/** The bot covers more than one product: every row names its product. */
		multiProduct?: boolean;
		/** Names each row's bot when the timeline spans several deployments. */
		deploymentLabel?: (deploymentId: string) => string | null;
		/** Bar clock for the empty-state sentence. */
		timeframe?: string | null;
		/** A discretionary deployment: no strategy rules, so no per-bar decisions. */
		discretionary?: boolean;
		pageSize?: number;
	} = $props();

	const uid = $props.id();

	const sourceKey = $derived(
		source.kind === 'deployment'
			? `deployment:${source.deploymentId}`
			: `strategy:${source.strategyId}:${source.deploymentId ?? '*'}`
	);

	let filter = $state<DecisionFilter>('all');
	let rows = $state<BarDecision[]>([]);
	let nextCursor = $state<string | null>(null);
	let storage = $state<DecisionStorage | null>(null);
	let phase = $state<'loading' | 'ready' | 'error'>('loading');
	let loadError = $state<string | null>(null);
	let loadingMore = $state(false);
	let moreError = $state<string | null>(null);
	let expanded = $state<Record<string, boolean>>({});
	let request = 0;

	$effect(() => {
		// Reload from the newest bar whenever the source or the filter changes.
		void sourceKey;
		void filter;
		untrack(() => void loadFirst());
	});

	function fetchPage(cursor: string | null): Promise<DecisionPage> {
		const query = { limit: pageSize, cursor, outcomes: decisionFilterOutcomes(filter) };
		return source.kind === 'deployment'
			? fetchDeploymentDecisions(source.deploymentId, query)
			: fetchStrategyDecisions(source.strategyId, { ...query, deploymentId: source.deploymentId });
	}

	async function loadFirst(): Promise<void> {
		const requestId = ++request;
		phase = 'loading';
		loadError = null;
		moreError = null;
		loadingMore = false;
		rows = [];
		nextCursor = null;
		storage = null;
		expanded = {};
		try {
			const page = await fetchPage(null);
			if (requestId !== request) return;
			rows = appendDecisionPage([], page.decisions);
			nextCursor = page.nextCursor;
			storage = page.storage;
			phase = 'ready';
		} catch (caught) {
			if (requestId !== request) return;
			loadError = caught instanceof Error ? caught.message : 'Decision history is unavailable.';
			phase = 'error';
		}
	}

	async function loadMore(): Promise<void> {
		const cursor = nextCursor;
		if (cursor === null || loadingMore) return;
		const requestId = request;
		loadingMore = true;
		moreError = null;
		try {
			const page = await fetchPage(cursor);
			if (requestId !== request) return;
			rows = appendDecisionPage(rows, page.decisions);
			nextCursor = page.nextCursor;
		} catch (caught) {
			if (requestId !== request) return;
			moreError = caught instanceof Error ? caught.message : 'Older decisions are unavailable.';
		} finally {
			if (requestId === request) loadingMore = false;
		}
	}

	function toggle(key: string): void {
		expanded = { ...expanded, [key]: !expanded[key] };
	}

	const showProduct = $derived(multiProduct || decisionProducts(rows).length > 1);
	const reasonRecords = $derived<readonly TradeReasonRecord[]>(
		reasons?.status === 'ready' ? reasons.records : []
	);
	const assignment = $derived(assignTradeReasons(rows, reasonRecords));
	const rowByKey = $derived(new Map(rows.map((row) => [decisionKey(row), row])));
	const settled = $derived(phase !== 'loading');
</script>

{#snippet barTime(value: string)}
	<time datetime={value} title="Bar closed {formatUtcTimestamp(value)}"
		><span class="sr-only">Bar closed</span>
		{formatUtcTimestamp(value).slice(5, 16)}<span class="utc">&nbsp;UTC</span></time
	>
{/snippet}

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

{#snippet reasonDetail(reason: TradeReasonRecord)}
	<div class="reason" data-testid="decision-trade-reason" data-intent-id={reason.intent_id}>
		<p>
			<b>{tradeReasonKindLabel(reason)}</b> · {reason.side}
			{reason.product_id} · {reason.origin} · recorded {formatUtcTimestamp(reason.created_at)}
		</p>
		<p>
			Risk {reason.risk.decision} ({reason.risk.reason_code}){reason.risk.detail.trim() === ''
				? ''
				: ` · ${reason.risk.detail}`} · {reason.risk.policy_source === 'published'
				? 'published risk policy'
				: 'compiled default risk policy'}
		</p>
		<p>{tradeReasonReconcileText(reason)}</p>
		{#if reason.reconcile.fills.length > 0}
			<ul class="plain">
				{#each reason.reconcile.fills as fill (fill.fill_id)}
					<li class="mono small">
						Fill {fill.quantity} @ {fill.price} · fee {fill.fee} · {formatUtcTimestamp(
							fill.filled_at
						)}
					</li>
				{/each}
			</ul>
		{/if}
		<p class="faint">{tradeReasonNotesText(reason)}</p>
	</div>
{/snippet}

{#snippet detail(decision: BarDecision, key: string, idBase: string)}
	{@const rule = decision.rule}
	{@const owned = assignment.byDecision.get(key) ?? []}
	{@const intents = decisionIntentIds(decision)}
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
					Protection {decision.protection_update.kind === 'replacement'
						? 'replacement'
						: 'canceled'}
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
				{@render reasonDetail(reason)}
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
{/snippet}

<div class="decisions" data-testid="decision-timeline">
	<div class="toolbar">
		<Segmented
			label="Show decisions"
			options={DECISION_FILTERS}
			value={filter}
			onchange={(next) => (filter = next)}
			testId="decision-filter"
		/>
		<span class="legend">Newest first · bar close times in UTC</span>
		<button
			type="button"
			class="btn ghost small"
			disabled={phase === 'loading'}
			onclick={() => void loadFirst()}>Refresh</button
		>
	</div>

	{#if phase === 'loading'}
		<p class="state quiet" data-testid="decision-loading" aria-busy="true">Loading decisions…</p>
	{:else if phase === 'error'}
		<div class="state problem-box" role="alert" data-testid="decision-error">
			<p>Decision history could not be loaded: {loadError}</p>
			<button type="button" class="btn" onclick={() => void loadFirst()}>Retry</button>
		</div>
	{:else if storage === 'unavailable'}
		<div class="state" data-testid="decision-storage-unavailable">
			<p class="title">Decision history is unavailable: no durable storage</p>
			<p class="quiet">
				This server runs without a database, so per-bar decisions are not journaled. The latest
				bar's signal is still reported on each bot.
			</p>
		</div>
	{:else if rows.length === 0}
		<p class="state quiet" data-testid="decision-empty">
			{discretionary && filter === 'all'
				? 'Discretionary orders run no strategy rules, so no per-bar decisions are journaled.'
				: decisionEmptyText(filter, timeframe)}
		</p>
	{:else}
		<ol class="rows" aria-label="Decisions, newest first">
			{#each rows as decision, index (decisionKey(decision))}
				{@const key = decisionKey(decision)}
				{@const idBase = `${uid}-decision-${index}`}
				{@const open = expanded[key] === true}
				{@const tone = decisionOutcomeTone(decision.outcome)}
				{@const bot = deploymentLabel?.(decision.deployment_id) ?? null}
				<li
					class="row"
					class:open
					data-testid="decision-row"
					data-outcome={decision.outcome}
					data-product={decision.product_id}
					data-deployment-id={decision.deployment_id}
				>
					<button
						type="button"
						class="row-head"
						aria-expanded={open}
						aria-controls={open ? `${idBase}-detail` : undefined}
						onclick={() => toggle(key)}
					>
						<span class="t">{@render barTime(decision.bar_closes_at)}</span>
						<span class="chip outcome tone-{tone}" data-testid="decision-outcome"
							>{decisionOutcomeLabel(decision.outcome)}</span
						>
						{#if showProduct}
							<span class="product" data-testid="decision-product">{decision.product_id}</span>
						{/if}
						{#if bot !== null}
							<span class="bot" data-testid="decision-bot">{bot}</span>
						{/if}
						<span class="summary" data-testid="decision-summary">{decision.summary}</span>
						<span class="caret" aria-hidden="true">›</span>
					</button>
					{#if open}
						<div class="detail" id="{idBase}-detail" data-testid="decision-detail">
							{@render detail(decision, key, idBase)}
						</div>
					{/if}
				</li>
			{/each}
		</ol>
		<div class="pager" data-testid="decision-pager">
			<span
				>Showing {rows.length} decision{rows.length === 1 ? '' : 's'}{nextCursor === null
					? ' · start of the journaled history'
					: ' · older decisions available'}</span
			>
			{#if nextCursor !== null}
				<button
					type="button"
					class="btn small"
					data-testid="decision-load-more"
					disabled={loadingMore}
					onclick={() => void loadMore()}>{loadingMore ? 'Loading…' : 'Load more'}</button
				>
			{/if}
		</div>
		{#if moreError !== null}
			<p class="state problem" role="alert" data-testid="decision-load-more-error">
				Older decisions could not be loaded: {moreError}
			</p>
		{/if}
	{/if}

	{#if filter === 'all' && settled}
		{#if reasons?.status === 'error'}
			<p class="state problem" role="status" data-testid="trade-reasons-error">
				Trade reasons could not be loaded: {reasons.message}
			</p>
		{:else if assignment.unmatched.length > 0}
			<section
				class="earlier"
				aria-labelledby="{uid}-earlier-title"
				data-testid="earlier-trade-reasons"
			>
				<h3 id="{uid}-earlier-title">
					{rows.length === 0 ? 'Trade reasons' : 'Earlier trade reasons'}
				</h3>
				{#if rows.length > 0}
					<p class="note">
						Persisted trade reasons no loaded decision row links: recorded before the decision
						journal existed, discretionary, or older than the rows loaded above.
					</p>
				{/if}
				<TradeReasonTimeline
					records={assignment.unmatched}
					label={rows.length === 0 ? 'Trade reasons, newest first' : 'Earlier trade reasons'}
					{deploymentLabel}
				/>
			</section>
		{/if}
	{/if}
</div>

<style>
	.toolbar {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 8px 12px;
		padding: 10px 16px;
		border-bottom: 1px solid var(--line);
	}
	.legend {
		margin-right: auto;
		color: var(--faint);
		font-size: var(--fs-sm);
	}
	.btn.small {
		min-height: 28px;
		padding: 0 10px;
		font-size: var(--fs-sm);
	}
	.state {
		margin: 0;
		padding: 14px 16px;
	}
	.state p {
		margin: 0;
	}
	.state .title {
		color: var(--text);
		font-weight: 500;
	}
	.state .quiet {
		margin-top: 4px;
	}
	.problem-box {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		justify-content: space-between;
		gap: 10px;
		color: var(--neg);
	}
	.rows {
		margin: 0;
		padding: 0;
		list-style: none;
	}
	.row {
		border-bottom: 1px solid var(--line);
	}
	.row-head {
		display: flex;
		align-items: center;
		gap: 10px;
		width: 100%;
		padding: 10px 16px;
		border: 0;
		background: transparent;
		color: var(--text);
		text-align: left;
		cursor: pointer;
	}
	.row-head:hover {
		background: var(--hover);
	}
	.row.open .row-head {
		background: var(--surface-2);
	}
	.t {
		flex: none;
		width: 118px;
		color: var(--faint);
		font-family: var(--font-mono);
		font-size: var(--fs-sm);
		white-space: nowrap;
	}
	.utc {
		font-size: var(--fs-xs);
	}
	.outcome {
		flex: none;
	}
	.chip.tone-pos {
		border-color: var(--accent-line);
		background: var(--accent-soft);
		color: var(--pos);
	}
	.chip.tone-neg {
		border-color: var(--danger-line);
		background: var(--danger-soft);
		color: var(--neg);
	}
	.chip.tone-warn {
		border-color: var(--warn-line);
		background: var(--warn-soft);
		color: var(--warn);
	}
	.chip.tone-info {
		border-color: var(--info-line);
		background: var(--info-soft);
		color: var(--info);
	}
	.chip.tone-muted {
		color: var(--muted);
	}
	.product {
		flex: none;
		color: var(--muted);
		font-family: var(--font-mono);
		font-size: var(--fs-xs);
	}
	.bot {
		flex: none;
		color: var(--faint);
		font-size: var(--fs-xs);
	}
	.summary {
		flex: 1;
		min-width: 0;
		overflow-wrap: anywhere;
	}
	.caret {
		flex: none;
		color: var(--faint);
		font-size: var(--fs-lg);
		line-height: 1;
	}
	.row.open .caret {
		transform: rotate(90deg);
	}
	.detail {
		display: grid;
		gap: 14px;
		/* Aligns with the outcome chip: row padding 16 + time column 118 + gap 10. */
		padding: 6px 16px 16px 144px;
		background: var(--surface-2);
	}
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
	.reason {
		display: grid;
		gap: 4px;
		padding: 8px 10px;
		border: 1px solid var(--line);
		border-radius: var(--radius-md);
		background: var(--surface);
	}
	.reason p {
		margin: 0;
	}
	.plain {
		margin: 0;
		padding: 0;
		list-style: none;
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
	.pager {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 10px;
		padding: 10px 16px;
		color: var(--faint);
		font-size: var(--fs-sm);
	}
	.earlier {
		border-top: 1px solid var(--line);
	}
	.earlier h3 {
		padding: 12px 16px 0;
	}
	.earlier .note {
		margin: 4px 0 0;
		padding: 0 16px;
		color: var(--faint);
		font-size: var(--fs-sm);
	}
	.mono {
		font-family: var(--font-mono);
	}
	.small {
		font-size: var(--fs-sm);
	}
	.quiet,
	.muted {
		color: var(--muted);
	}
	.faint {
		color: var(--faint);
		font-size: var(--fs-sm);
	}
	.problem {
		color: var(--neg);
	}
	@media (prefers-reduced-motion: no-preference) {
		.caret {
			transition: transform 0.12s;
		}
	}
	@media (max-width: 640px) {
		.row-head {
			flex-wrap: wrap;
			gap: 6px 10px;
		}
		.t {
			width: auto;
		}
		.summary {
			flex-basis: 100%;
			order: 5;
		}
		.detail {
			padding-left: 16px;
		}
	}
</style>
