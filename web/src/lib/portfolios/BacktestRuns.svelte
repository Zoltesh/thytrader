<script lang="ts">
	/**
	 * Earlier portfolio backtest runs, newest first: when, net return, drawdown,
	 * sleeves and revision. Choosing one shows its result above.
	 */
	import {
		drawdownPercent,
		signedPercent,
		utcMinute,
		type PortfolioBacktestListing
	} from '$lib/portfolios';
	import { tone } from './backtest';

	let {
		listings,
		selected,
		onselect
	}: {
		listings: PortfolioBacktestListing[];
		/** Result fingerprint of the run shown above, or null. */
		selected: string | null;
		/** Show one earlier run's result. */
		onselect: (fingerprint: string) => void;
	} = $props();
</script>

<section class="card" aria-label="Earlier runs">
	<div class="card-head"><h2>Earlier runs</h2></div>
	<ul class="runs" data-testid="backtest-runs">
		{#each listings as listing (listing.result_fingerprint)}
			<li>
				<button
					type="button"
					class="run-row"
					aria-current={listing.result_fingerprint === selected ? 'true' : undefined}
					onclick={() => onselect(listing.result_fingerprint)}
				>
					<span class="mono">{utcMinute(listing.published_at)}</span>
					<span class={tone(listing.total_return_fraction)}
						>{signedPercent(listing.total_return_fraction)}</span
					>
					<span class="faint">DD {drawdownPercent(listing.maximum_drawdown_fraction)}</span>
					<span class="faint"
						>{listing.sleeve_count} sleeves · rev {listing.portfolio_revision}</span
					>
				</button>
			</li>
		{/each}
	</ul>
</section>

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
	.runs {
		margin: 0;
		padding: 0;
		list-style: none;
	}
	.run-row {
		display: grid;
		grid-template-columns: 170px 90px 110px 1fr;
		gap: 12px;
		width: 100%;
		padding: 10px 16px;
		border: 0;
		border-top: 1px solid var(--line);
		background: transparent;
		color: var(--text);
		text-align: left;
		cursor: pointer;
	}
	.runs li:first-child .run-row {
		border-top: 0;
	}
	.run-row:hover {
		background: var(--hover);
	}
	.run-row[aria-current='true'] {
		background: var(--accent-soft);
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
</style>
