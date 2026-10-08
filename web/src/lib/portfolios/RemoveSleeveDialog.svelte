<script lang="ts">
	/** Confirmation to remove one sleeve; the strategy and its backtests stay in the library. */
	import ConfirmDialog from '$lib/ConfirmDialog.svelte';
	import { weightPercent, type Portfolio, type PortfolioSleeve } from '$lib/portfolios';

	let {
		portfolio,
		removeTarget,
		pending,
		error,
		oncancel,
		onconfirm
	}: {
		portfolio: Portfolio;
		/** The sleeve to remove; the dialog is open while it is set. */
		removeTarget: PortfolioSleeve | null;
		pending: boolean;
		error: string | null;
		oncancel: () => void;
		onconfirm: () => void;
	} = $props();
</script>

<ConfirmDialog
	open={removeTarget !== null}
	title="Remove sleeve?"
	tone="danger"
	confirmLabel="Remove sleeve"
	pendingLabel="Removing…"
	{pending}
	{error}
	testId="remove-sleeve-dialog"
	{oncancel}
	{onconfirm}
>
	{#if removeTarget !== null}
		<p>
			Removes “{removeTarget.strategy_name}” ({weightPercent(removeTarget.weight_fraction)}) from {portfolio.name}.
			The strategy and its backtests stay in your library; the journal records the removal.
		</p>
	{/if}
</ConfirmDialog>
