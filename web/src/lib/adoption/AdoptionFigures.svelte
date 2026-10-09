<script lang="ts">
	/**
	 * What an adoption would see (ADR 0124): the Coinbase balance of the coin, how much live
	 * bots already manage (positions and unfilled opening orders), the unmanaged remainder,
	 * the adoptable quantity, and the closed-candle mark. Unknown figures read "Unknown",
	 * never zero, and the action's blocking reasons are listed.
	 */
	import { formatQuantityDisplay } from '$lib/money';
	import { blockingReasons, type AdoptionAction, type AdoptionPreview } from '$lib/adoption';

	let {
		preview,
		loading,
		error,
		action
	}: {
		preview: AdoptionPreview | null;
		loading: boolean;
		error: string | null;
		action: AdoptionAction;
	} = $props();

	const reasons = $derived(blockingReasons(preview, action));

	function quantity(value: string | null | undefined): string {
		return value === null || value === undefined ? 'Unknown' : formatQuantityDisplay(value).text;
	}
</script>

<div class="figures" data-testid="adoption-figures" aria-busy={loading}>
	{#if error !== null}
		<p class="error" role="alert">Preview unavailable: {error}</p>
	{:else if preview === null}
		<p class="muted">{loading ? 'Reading Coinbase balance and bot claims…' : 'No preview yet.'}</p>
	{:else}
		<div class="row">
			<span>Coinbase balance</span>
			<b data-testid="adoption-balance">{quantity(preview.balance_total)} {preview.base_currency}</b
			>
		</div>
		<div class="row">
			<span>Managed by bots</span>
			<b data-testid="adoption-managed">{quantity(preview.claims?.claimed)}</b>
		</div>
		<div class="row">
			<span>Unmanaged</span>
			<b data-testid="adoption-unmanaged">{quantity(preview.unmanaged)}</b>
		</div>
		<div class="row">
			<span>Adoptable now</span>
			<b data-testid="adoption-adoptable">{quantity(preview.adoptable)}</b>
		</div>
		<div class="row">
			<span>Mark ({preview.timeframe} close)</span>
			<b data-testid="adoption-mark">{preview.mark ?? 'Unknown'}</b>
		</div>
		{#each reasons as reason, index (index)}
			<p class="warning" role="status" data-testid="adoption-blocking">{reason}</p>
		{/each}
	{/if}
</div>

<style>
	.figures {
		display: grid;
		gap: 2px;
	}
	.row {
		display: flex;
		gap: 10px;
		padding: 6px 0;
		border-bottom: 1px solid var(--line);
	}
	.row span {
		flex: 1;
		color: var(--muted);
	}
	.row b {
		font-weight: 500;
		font-variant-numeric: tabular-nums;
		text-align: right;
	}
	.muted {
		margin: 0;
		color: var(--faint);
	}
	.warning,
	.error {
		margin: 6px 0 0;
		color: var(--warn);
		font-size: var(--fs-sm);
	}
</style>
