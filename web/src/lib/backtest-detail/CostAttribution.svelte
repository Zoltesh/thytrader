<script lang="ts">
	/**
	 * Trading-fee attribution of a backtest result: PnL before fees, entry and exit
	 * fees, closed-trade net PnL, and any accounting reconciliation residual.
	 */
	import type { BacktestCostAttribution } from '$lib/backtests';
	import { formatUsd } from '$lib/portfolio';

	let { attribution }: { attribution: BacktestCostAttribution | null } = $props();
</script>

<div class="cost-attribution" data-testid="result-cost-attribution">
	<h3>Trading fees</h3>
	{#if attribution}
		<p>
			{attribution.trade_count} closed trades · modeled fill prices already include spread and slippage.
			Amounts are in the strategy's quote currency.
		</p>
		<div class="metrics-row">
			<div class="metric">
				<div class="l">PnL before trading fees</div>
				<div class="v">{formatUsd(attribution.fill_price_pnl_before_fees)}</div>
			</div>
			<div class="metric">
				<div class="l">Entry fees</div>
				<div class="v">{formatUsd(attribution.entry_fees)}</div>
			</div>
			<div class="metric">
				<div class="l">Exit fees</div>
				<div class="v">{formatUsd(attribution.exit_fees)}</div>
			</div>
			<div class="metric">
				<div class="l">Closed-trade net PnL</div>
				<div class="v">{formatUsd(attribution.net_pnl)}</div>
			</div>
		</div>
		{#if attribution.accounting_residual !== '0' || attribution.summary_net_pnl_delta !== '0'}
			<details>
				<summary>Accounting reconciliation</summary>
				<p>
					Recorded net minus before-fees PnL less both fees: <code
						>{attribution.accounting_residual}</code
					>. Summary net minus closed-trade net:
					<code>{attribution.summary_net_pnl_delta}</code>. Decimal rounding can produce small
					differences; these are reported separately from fees.
				</p>
			</details>
		{/if}
	{:else}
		<p>Fee attribution is unavailable for this result.</p>
	{/if}
</div>

<style>
	.metrics-row {
		display: grid;
		grid-template-columns: repeat(6, minmax(0, 1fr));
		gap: 12px;
	}
	.cost-attribution {
		padding: 16px 18px;
		border: 1px solid var(--line);
		border-radius: 13px;
		background: var(--surface);
	}
	.cost-attribution h3 {
		margin: 0;
	}
	.cost-attribution p {
		margin: 8px 0 12px;
		color: var(--muted);
		font-size: var(--fs-sm);
	}
	.cost-attribution .metrics-row {
		grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
	}
	.metric .l {
		color: var(--muted);
		font-size: var(--fs-sm);
	}
	.metric .v {
		margin-top: 2px;
		font-size: var(--fs-xl);
		font-weight: 600;
		letter-spacing: -0.01em;
	}
	h3 {
		margin: 0;
		font-size: var(--fs-md);
	}
	p {
		margin: 0;
		color: var(--faint);
		font-size: 12px;
	}
	code {
		color: var(--code);
	}
	@media (max-width: 1100px) {
		.metrics-row {
			grid-template-columns: repeat(3, minmax(0, 1fr));
		}
	}
	@media (max-width: 520px) {
		.metrics-row {
			grid-template-columns: repeat(2, minmax(0, 1fr));
		}
	}
</style>
