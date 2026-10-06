<script lang="ts">
	import {
		counterfactualFeeLabel,
		evidenceReasonLabel,
		liquidityLabel,
		slippageLabel,
		type ExecutionQualityReport,
		type ExecutionTwinComparison
	} from '$lib/executionQuality';
	import { formatUsd } from '$lib/portfolio';
	import { formatUtcTimestamp } from '$lib/time';

	let {
		report,
		comparison = null,
		comparisonError = null
	}: {
		report: ExecutionQualityReport;
		comparison?: ExecutionTwinComparison | null;
		comparisonError?: string | null;
	} = $props();
</script>

<section class="quality" data-testid="execution-quality-report" aria-label="Execution quality">
	<p class="note">
		Recorded fills only. Missing fees, liquidity, and journaled closes are omitted, never shown as
		zero. Slippage uses the persisted intent's completed decision-bar close, not a future fill-bar
		close or a quote at a later reprice. This page cannot place or change orders.
	</p>
	<div class="totals" data-testid="execution-quality-totals">
		<div>
			<span>Before fees</span><strong>{formatUsd(report.totals.fill_price_pnl_before_fees)}</strong>
		</div>
		<div><span>Entry fees</span><strong>{formatUsd(report.totals.entry_fees)}</strong></div>
		<div><span>Exit fees</span><strong>{formatUsd(report.totals.exit_fees)}</strong></div>
		<div><span>Net</span><strong>{formatUsd(report.totals.net_pnl)}</strong></div>
		<div>
			<span>Slippage</span>
			<strong>{slippageLabel(report.totals.weighted_slippage_bps)}</strong>
		</div>
	</div>
	{#if !report.evidence.complete}
		<ul class="reasons" data-testid="execution-quality-reasons">
			{#each report.evidence.reasons as reason (reason)}
				<li>{evidenceReasonLabel(reason)}</li>
			{/each}
		</ul>
	{:else}
		<p data-testid="execution-quality-complete">Evidence complete for the recorded fills.</p>
	{/if}
	{#each report.books as book (book.product_id)}
		<h3>{book.product_id}</h3>
		{#if book.open_cycle}
			<p data-testid="open-cycle">
				Open {book.open_cycle.direction}
				{book.open_cycle.quantity}. Entry fees
				{formatUsd(book.open_cycle.entry_fees)}. {book.open_cycle.exits.length} recorded partial exit
				fills are included below. No remaining-position exit PnL is invented.
			</p>
		{/if}
		{#if book.round_trips.length === 0}
			<p class="quiet">No closed round trip on this product.</p>
		{:else}
			<table>
				<thead>
					<tr>
						<th>Closed</th>
						<th>Direction</th>
						<th>Before fees</th>
						<th>Entry fees</th>
						<th>Exit fees</th>
						<th>Net</th>
						<th>Slippage</th>
						<th>Liquidity</th>
					</tr>
				</thead>
				<tbody>
					{#each book.round_trips as trip, index (`${trip.closed_at}-${index}`)}
						<tr data-testid="round-trip">
							<td>{formatUtcTimestamp(trip.closed_at)}</td>
							<td>{trip.direction}</td>
							<td>{formatUsd(trip.fill_price_pnl_before_fees)}</td>
							<td>{formatUsd(trip.entry_fees)}</td>
							<td>{formatUsd(trip.exit_fees)}</td>
							<td>{formatUsd(trip.net_pnl)}</td>
							<td>{slippageLabel(trip.slippage_bps)}</td>
							<td>
								{trip.entries.map((fill) => liquidityLabel(fill.liquidity)).join(', ')} /
								{trip.exits.map((fill) => liquidityLabel(fill.liquidity)).join(', ')}
							</td>
						</tr>
					{/each}
				</tbody>
			</table>
		{/if}
		<details data-testid="recorded-fill-evidence">
			<summary>All {book.recorded_fills.length} applied fills (including partial exits)</summary>
			<table>
				<thead
					><tr
						><th>Fill time</th><th>Side / quantity</th><th>Recorded fee</th><th
							>Decision reference</th
						></tr
					></thead
				>
				<tbody>
					{#each book.recorded_fills as fill (fill.fill_id)}
						<tr
							><td>{formatUtcTimestamp(fill.filled_at)}</td><td>{fill.side} {fill.quantity}</td><td
								>{fill.fee}</td
							><td>
								{#if fill.reference_price !== null && fill.reference_bar_closes_at !== null}
									{fill.reference_price} · bar completed {formatUtcTimestamp(
										fill.reference_bar_closes_at
									)}
								{:else}No causal decision close{/if}
							</td></tr
						>
					{/each}
				</tbody>
			</table>
		</details>
	{/each}
	{#if comparison}
		<aside data-testid="execution-twin" class:cannot-compare={!comparison.comparable}>
			<h3>{comparison.comparable ? 'Twin comparison' : 'Cannot compare twins'}</h3>
			<p>
				Recorded-fill lifetime summaries{comparison.summaries_context_only
					? ' — context only; unequal or unverified histories must not be compared.'
					: ' — aligned evidence populations.'}
			</p>
			<p>
				Paper net {formatUsd(comparison.paper.net_pnl)} · live net {formatUsd(
					comparison.live.net_pnl
				)}
			</p>
			{#if comparison.fee_normalization}
				<p>
					All {comparison.fee_normalization.fill_count} applied lifetime live fills, independently of
					twin overlap: observed fees {formatUsd(comparison.fee_normalization.observed_live_fees)}.
					Counterfactual at paper rates
					{#if comparison.fee_normalization.counterfactual_live_fees_at_paper_rates !== null}
						{formatUsd(comparison.fee_normalization.counterfactual_live_fees_at_paper_rates)}
					{:else}
						{counterfactualFeeLabel(null)}
					{/if}
					({comparison.fee_normalization.rate_source.replaceAll('_', ' ')}). Realized PnL is
					unchanged.
				</p>
			{/if}
			{#if comparison.reasons.length > 0}
				<ul>
					{#each comparison.reasons as reason (reason)}
						<li>{reason.replaceAll('_', ' ')}</li>
					{/each}
				</ul>
			{/if}
		</aside>
	{:else if comparisonError}
		<p data-testid="execution-twin-unavailable">{comparisonError}</p>
	{/if}
</section>

<style>
	.quality {
		display: grid;
		gap: 12px;
	}
	.note,
	.quiet {
		color: var(--muted, #667);
	}
	.totals {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
		gap: 8px;
	}
	.totals div {
		display: grid;
		gap: 4px;
		padding: 10px 12px;
		border: 1px solid var(--line, #ddd);
	}
	.totals span {
		font-size: 12px;
	}
	.reasons {
		margin: 0;
		padding-left: 18px;
	}
	table {
		width: 100%;
		border-collapse: collapse;
		font-size: 13px;
	}
	th,
	td {
		padding: 6px 8px;
		border-bottom: 1px solid var(--line, #ddd);
		text-align: left;
	}
	.cannot-compare {
		border: 1px solid var(--warn, #a60);
		padding: 12px;
	}
</style>
