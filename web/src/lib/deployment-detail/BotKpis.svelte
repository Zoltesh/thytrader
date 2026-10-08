<script lang="ts">
	/**
	 * The four bot KPI cards: allocated capital, PnL from the operator
	 * performance report (or an honest ledger fallback when it is unavailable),
	 * position with protection and the held book's uPnL, and the latest bar with
	 * when the next evaluation is due.
	 */
	import { nextEvaluationText } from '$lib/decisions';
	import {
		drawdownIsCaveated,
		lastEvaluatedText,
		latestBarHeadline,
		ledgerPerformanceText,
		performanceCurrencySuffix,
		performanceHeadline,
		performanceReportText,
		productIdQuote,
		quoteAmountLabel
	} from '$lib/deployment-detail';
	import { pnlOf, positionText, protectionText } from '$lib/deployment-portfolio';
	import type { Deployment, DeploymentPosition, OperatorPerformanceReport } from '$lib/deployments';
	import { heldText, markTitle, unrealizedText } from '$lib/open-books';

	let {
		current,
		positions,
		now,
		performanceReport,
		performanceError,
		performanceLoading
	}: {
		current: Deployment;
		positions: DeploymentPosition[];
		/** Clock for the held time (ADR 0098). */
		now: number;
		performanceReport: OperatorPerformanceReport | null;
		performanceError: string | null;
		performanceLoading: boolean;
	} = $props();

	const isLive = $derived(current.mode === 'live');
	const performancePayload = $derived(performanceReport?.payload ?? null);
</script>

<section class="kpis" aria-label="Key figures">
	<article class="card kpi" data-testid="kpi-capital">
		<h2 class="label">{isLive ? 'Allocated capital' : 'Allocated capital (paper)'}</h2>
		{#if current.capital && (current.capital.allocated_capital || current.capital.performance_equity)}
			<p class="value">
				{quoteAmountLabel(current.capital.allocated_capital, current.product_id)}
			</p>
			<p class="delta">
				Performance equity {quoteAmountLabel(
					current.capital.performance_equity,
					current.product_id
				)}{isLive
					? ` · venue ${quoteAmountLabel(current.capital.venue_available_quote, current.product_id)}`
					: ' · simulated'}
			</p>
		{:else}
			<p class="value">—</p>
			<p class="delta">No capital accounting on this snapshot.</p>
		{/if}
	</article>
	<article class="card kpi" data-testid="kpi-pnl">
		<h2 class="label">PnL</h2>
		{#if performanceLoading && performanceReport === null}
			<p class="value">—</p>
			<p class="delta" data-testid="performance-loading">Loading operator performance…</p>
		{:else if performanceError !== null}
			{@const fallback = pnlOf(current)}
			<p class="value" class:pos={fallback.tone === 'pos'} class:neg={fallback.tone === 'neg'}>
				{fallback.text}
			</p>
			<p class="delta problem" data-testid="performance-error" role="status">
				Operator performance report unavailable ({performanceError}). Ledger summary:
				{ledgerPerformanceText(current)}
			</p>
		{:else if performancePayload}
			{@const headline = performanceHeadline(performancePayload)}
			<p class="value" class:pos={headline.startsWith('+')} class:neg={headline.startsWith('-')}>
				{headline}
			</p>
			<p class="delta" data-testid="deployment-performance">
				{performanceReportText(performancePayload)}
			</p>
			<p class="delta">
				Fill ledger{performanceCurrencySuffix(performancePayload.currency).trim() === ''
					? ' · quote currency unknown'
					: ''} · {isLive ? 'after Coinbase fees' : 'assumed paper fees'}
			</p>
			{#if drawdownIsCaveated(performancePayload)}
				<p class="delta" data-testid="performance-drawdown-caveat">
					Drawdown {(Number(performancePayload.maximum_drawdown_fraction) * 100).toFixed(2)}% from
					fill-event marks, not a bar equity curve; it understates intra-bar drawdown.
				</p>
			{/if}
			{#each performanceReport?.partial_result_warnings ?? [] as warning (warning)}
				<p class="delta contract-note" role="status">{warning}</p>
			{/each}
		{:else}
			<p class="value">—</p>
			<p class="delta">{ledgerPerformanceText(current)}</p>
		{/if}
	</article>
	<article class="card kpi" data-testid="kpi-position">
		<h2 class="label">Position</h2>
		<p class="value small">{positionText(positions)}</p>
		<p class="delta">{protectionText(current, positions)}</p>
		{#if positions.length === 1}
			{@const book = positions[0]!}
			{@const pnl = unrealizedText(book, productIdQuote(book.product_id) ?? '')}
			{@const held = `held ${heldText(book.entered_bar, now)}`}
			<p class="delta" data-testid="kpi-position-pnl">
				{#if pnl !== null}<span class="upnl {pnl.tone}" title={markTitle(book)}
						>uPnL {pnl.text}</span
					>{` · ${held}`}{:else}{held}{/if}
			</p>
		{/if}
	</article>
	<article class="card kpi" data-testid="kpi-latest-bar">
		<h2 class="label">Latest bar</h2>
		<p class="value small">{latestBarHeadline(current)}</p>
		<p class="delta">{lastEvaluatedText(current)}</p>
		{#if current.kind !== 'discretionary'}
			<p class="delta" data-testid="next-evaluation">{nextEvaluationText(current)}</p>
		{/if}
	</article>
</section>

<style>
	.upnl {
		font-variant-numeric: tabular-nums;
	}
	.upnl.pos {
		color: var(--pos);
	}
	.upnl.neg {
		color: var(--neg);
	}
	.kpis {
		display: grid;
		grid-template-columns: repeat(4, minmax(0, 1fr));
		gap: 12px;
		margin-bottom: 16px;
	}
	.kpi {
		min-width: 0;
		padding: 14px 16px;
	}
	.kpi .label {
		color: var(--muted);
		font-size: var(--fs-sm);
		font-weight: 400;
	}
	.kpi .value {
		margin: 4px 0 0;
		color: var(--text);
		font-size: var(--fs-2xl);
		font-weight: 600;
		letter-spacing: -0.02em;
		overflow-wrap: anywhere;
	}
	.kpi .value.pos {
		color: var(--pos);
	}
	.kpi .value.neg {
		color: var(--neg);
	}
	.kpi .value.small {
		font-size: var(--fs-xl);
	}
	.kpi .delta {
		margin: 2px 0 0;
		color: var(--faint);
		font-size: var(--fs-sm);
	}
	.card {
		margin-bottom: 16px;
	}
	.kpis > .card {
		margin-bottom: 0;
	}
	.muted {
		color: var(--muted);
	}
	.pos {
		color: var(--pos);
	}
	.neg,
	.problem {
		color: var(--neg);
	}
	.contract-note {
		margin: 0 0 12px;
		color: var(--warn);
		font-size: var(--fs-sm);
	}
	.kpi .contract-note {
		margin: 2px 0 0;
	}
	@media (max-width: 1100px) {
		.kpis {
			grid-template-columns: repeat(2, minmax(0, 1fr));
		}
	}
	@media (max-width: 560px) {
		.kpis {
			grid-template-columns: minmax(0, 1fr);
		}
	}
</style>
