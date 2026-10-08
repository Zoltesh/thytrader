<script lang="ts" module>
	import type { DecisionTimelineSource as Source } from './decision-timeline/view';

	/** Which journal to read: one bot, or every bot of a strategy (optionally one). */
	export type DecisionTimelineSource = Source;
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
	 *
	 * Rows render as `decision-timeline/DecisionRow.svelte` (expanding into
	 * `DecisionDetail.svelte`); pure helpers live in `decision-timeline/view.ts`.
	 */
	import { untrack } from 'svelte';
	import Segmented from '$lib/Segmented.svelte';
	import TradeReasonTimeline from '$lib/TradeReasonTimeline.svelte';
	import {
		DECISION_FILTERS,
		DECISION_PAGE_SIZE,
		appendDecisionPage,
		assignTradeReasons,
		decisionEmptyText,
		decisionFilterOutcomes,
		decisionKey,
		decisionProducts,
		fetchDeploymentDecisions,
		fetchStrategyDecisions,
		type BarDecision,
		type DecisionFilter,
		type DecisionPage,
		type DecisionStorage
	} from '$lib/decisions';
	import type { TradeReasonRecord } from '$lib/memory';
	import type { TradeReasonState } from '$lib/trade-reasons';
	import DecisionRow from './decision-timeline/DecisionRow.svelte';
	import { decisionPagerText, decisionSourceKey } from './decision-timeline/view';

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

	const sourceKey = $derived(decisionSourceKey(source));

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
				<DecisionRow
					{decision}
					{key}
					idBase={`${uid}-decision-${index}`}
					open={expanded[key] === true}
					{showProduct}
					bot={deploymentLabel?.(decision.deployment_id) ?? null}
					{assignment}
					{rowByKey}
					{reasons}
					ontoggle={() => toggle(key)}
				/>
			{/each}
		</ol>
		<div class="pager" data-testid="decision-pager">
			<span>{decisionPagerText(rows.length, nextCursor !== null)}</span>
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
	h3 {
		margin: 0;
		color: var(--muted);
		font-size: var(--fs-xs);
		font-weight: 600;
		letter-spacing: 0.06em;
		text-transform: uppercase;
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
	.small {
		font-size: var(--fs-sm);
	}
	.quiet {
		color: var(--muted);
	}
	.problem {
		color: var(--neg);
	}
</style>
