<script lang="ts">
	/**
	 * Correlation of sleeve returns (a sleeve-by-sleeve matrix) beside overlap —
	 * how often sleeves are long the same asset or long together — and the
	 * equal-weight basket's buy-and-hold legs.
	 */
	import { formatPercent } from '$lib/portfolio';
	import {
		coefficientText,
		durationText,
		pairCoefficient,
		signedPercent,
		type PortfolioBacktestResult
	} from '$lib/portfolios';
	import { tone } from './backtest';

	let { result }: { result: PortfolioBacktestResult } = $props();
</script>

<div class="grid2">
	<section class="card" aria-label="Correlation">
		<div class="card-head">
			<h2>Correlation of sleeve returns</h2>
			<span class="faint small"
				>{result.correlation.observations} returns on a {durationText(
					result.correlation.return_clock_seconds
				)} clock</span
			>
		</div>
		{#if result.sleeves.length < 2}
			<p class="pad faint">Correlation needs at least two sleeves.</p>
		{:else}
			<div class="table-wrap">
				<table data-testid="correlation-table">
					<thead>
						<tr>
							<th scope="col"><span class="sr-only">Sleeve</span></th>
							{#each result.sleeves as column (column.sleeve_id)}
								<th scope="col">{column.strategy_name}</th>
							{/each}
						</tr>
					</thead>
					<tbody>
						{#each result.sleeves as row (row.sleeve_id)}
							<tr>
								<th scope="row" class="row-head">{row.strategy_name}</th>
								{#each result.sleeves as column (column.sleeve_id)}
									<td
										>{row.sleeve_id === column.sleeve_id
											? '—'
											: coefficientText(
													pairCoefficient(result.correlation, row.sleeve_id, column.sleeve_id)
												)}</td
									>
								{/each}
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
		{/if}
	</section>
	<section class="card body" aria-label="Overlap and basket">
		<h2>Overlap</h2>
		<div class="check">
			<span class="muted">Two sleeves long the same asset</span>
			<span data-testid="overlap-same-asset"
				>{formatPercent(result.overlap.same_asset_fraction)} of the time</span
			>
		</div>
		<div class="check">
			<span class="muted">Any two sleeves long together</span>
			<span>{formatPercent(result.overlap.long_together_fraction)} of the time</span>
		</div>
		{#if result.overlap.excluded_sleeve_ids.length > 0}
			<p class="faint small">
				{result.overlap.excluded_sleeve_ids.length} multi-product sleeve{result.overlap
					.excluded_sleeve_ids.length === 1
					? ' is'
					: 's are'} excluded from overlap.
			</p>
		{/if}
		<h2 class="sub">Equal-weight basket</h2>
		{#each result.basket.legs as leg (leg.product_id)}
			<div class="check">
				<span class="muted">{leg.product_id} buy &amp; hold</span>
				<span class={tone(leg.return_fraction)}>{signedPercent(leg.return_fraction)}</span>
			</div>
		{/each}
	</section>
</div>

<style>
	.card {
		margin-top: 16px;
	}
	.card-head {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 10px;
		padding: 14px 16px;
		border-bottom: 1px solid var(--line);
	}
	.grid2 {
		display: grid;
		grid-template-columns: minmax(0, 1.2fr) minmax(0, 1fr);
		gap: 16px;
	}
	.body {
		padding: 16px 18px;
	}
	.sub {
		margin-top: 16px;
	}
	.check {
		display: flex;
		gap: 10px;
		padding: 8px 0;
		border-top: 1px solid var(--line);
		font-size: var(--fs-sm);
	}
	.check > span:first-child {
		flex: 1;
	}
	.row-head {
		color: var(--muted);
		font-weight: 500;
		text-align: left;
		padding: 11px 16px;
		border-top: 1px solid var(--line);
		font-size: var(--fs-base);
	}
	.pad {
		padding: 0 16px 14px;
	}
	.pos {
		color: var(--pos);
	}
	.neg {
		color: var(--neg);
	}
	.faint {
		color: var(--faint);
	}
	.muted {
		color: var(--muted);
	}
	.small {
		font-size: var(--fs-sm);
	}
	@media (max-width: 1100px) {
		.grid2 {
			grid-template-columns: minmax(0, 1fr);
		}
	}
</style>
