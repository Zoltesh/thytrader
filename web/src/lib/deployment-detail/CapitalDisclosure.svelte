<script lang="ts">
	/**
	 * Capital breakdown disclosure: every capital field on the snapshot plus
	 * ledger cash (and paper starting cash and assumed fees for paper bots).
	 */
	import { capitalBreakdown, quoteAmountLabel } from '$lib/deployment-detail';
	import type { Deployment } from '$lib/deployments';

	let { current }: { current: Deployment } = $props();

	const isLive = $derived(current.mode === 'live');
	const capitalRows = $derived(capitalBreakdown(current));
</script>

<details class="card disclosure" data-testid="capital-disclosure">
	<summary>Capital breakdown</summary>
	<div class="pad">
		{#if capitalRows === null}
			<p class="quiet">No capital accounting on this snapshot.</p>
		{:else}
			<dl class="kv">
				{#each capitalRows as item (item.label)}
					<div>
						<dt>{item.label}</dt>
						<dd>{item.value}</dd>
					</div>
				{/each}
			</dl>
		{/if}
		<dl class="kv">
			<div>
				<dt>Ledger cash</dt>
				<dd>{quoteAmountLabel(current.cash, current.product_id)}</dd>
			</div>
			{#if !isLive}
				<div>
					<dt>Paper starting cash</dt>
					<dd>{quoteAmountLabel(current.paper_starting_cash, current.product_id)}</dd>
				</div>
				<div>
					<dt>Assumed maker / taker fee</dt>
					<dd>{current.maker_fee_rate ?? 'default'} / {current.taker_fee_rate ?? 'default'}</dd>
				</div>
			{/if}
		</dl>
		<p class="quiet small">
			Live books size from allocated capital or venue available quote, not ledger cash.
		</p>
	</div>
</details>

<style>
	.card {
		margin-bottom: 16px;
	}
	.pad {
		margin: 0;
		padding: 14px 16px;
	}
	.quiet {
		color: var(--muted);
	}
	.small {
		font-size: var(--fs-sm);
	}
	.disclosure summary {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 6px 12px;
		padding: 12px 16px;
		cursor: pointer;
		font-weight: 600;
	}
	.disclosure[open] summary {
		border-bottom: 1px solid var(--line);
	}
	.kv {
		display: flex;
		flex-wrap: wrap;
		gap: 10px 28px;
		margin: 0 0 12px;
	}
	.kv dt {
		color: var(--faint);
		font-size: var(--fs-xs);
	}
	.kv dd {
		margin: 2px 0 0;
		font-family: var(--font-mono);
		font-size: var(--fs-sm);
	}
</style>
