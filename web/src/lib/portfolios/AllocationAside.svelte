<script lang="ts">
	/** Allocation aside: sleeve / reserve / unallocated bars, the largest single asset, and the allocated total. */
	import {
		allocationAriaLabel,
		largestAssetText,
		quoteText,
		weightPercent,
		type AllocationBar,
		type Portfolio
	} from '$lib/portfolios';

	let { portfolio, bars }: { portfolio: Portfolio; bars: AllocationBar[] } = $props();

	const largest = $derived(largestAssetText(portfolio));
</script>

<aside class="card aside" aria-label="Allocation">
	<h2>Allocation</h2>
	<div class="bars" role="img" aria-label={allocationAriaLabel(bars)} data-testid="allocation-bars">
		{#each bars as bar (bar.key)}
			<div class="bar">
				<div class="bar-label">
					<span class="muted">{bar.label}</span><span class="pct">{bar.percent}</span>
				</div>
				<div class="track">
					<div class="fill {bar.tone}" style:width={bar.width}></div>
				</div>
			</div>
		{/each}
	</div>
	<div class="check">
		<span class="muted">Largest single asset</span>
		<span data-testid="largest-asset" class:warn={largest.over}>{largest.text}</span>
	</div>
	<div class="check">
		<span class="muted">Allocated to sleeves</span>
		<span
			>{quoteText(portfolio.allocation.allocated_quote, portfolio.quote_currency)} ({weightPercent(
				portfolio.allocation.allocated_fraction
			)})</span
		>
	</div>
	{#if largest.over}
		<p class="warn small">
			The largest asset is above this portfolio's per-asset limit. Limits bind orders only once
			portfolio deployment arrives.
		</p>
	{/if}
</aside>

<style>
	.aside {
		padding: 16px 18px;
	}
	.bars {
		display: flex;
		flex-direction: column;
		gap: 12px;
		margin: 14px 0 10px;
	}
	.bar-label {
		display: flex;
		gap: 8px;
		font-size: var(--fs-sm);
	}
	.pct {
		margin-left: auto;
		font-weight: 500;
	}
	.track {
		height: 6px;
		margin-top: 5px;
		border-radius: 3px;
		background: var(--surface-2);
	}
	.fill {
		height: 6px;
		border-radius: 3px;
	}
	.fill.sleeve {
		background: var(--accent);
	}
	.fill.reserve {
		background: var(--line-strong);
	}
	.fill.unallocated {
		background: var(--line-2);
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
	.muted {
		color: var(--muted);
	}
	.small {
		font-size: var(--fs-sm);
	}
	.warn {
		color: var(--warn);
	}
</style>
