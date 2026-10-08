<script lang="ts">
	/**
	 * Delete confirmation for one strategy or the selection. It lists the
	 * server's dry run: what each strategy takes with it, which are blocked by
	 * running or paused bots (with links to them), and that live history is kept.
	 */
	import { resolve } from '$app/paths';
	import ConfirmDialog from '$lib/ConfirmDialog.svelte';
	import {
		bulkOutcomeText,
		deletionCountsText,
		type BulkDeleteItem,
		type StrategyLibraryEntry
	} from '$lib/strategies';

	let {
		deleteTargets,
		deletePreview,
		deletePreviewLoading,
		deletable,
		blockedCount,
		deleting,
		deleteError,
		nameOf,
		oncancel,
		onconfirm
	}: {
		/** Strategies the dialog is about; open while non-empty. */
		deleteTargets: StrategyLibraryEntry[];
		/** The dry run's per-strategy outcomes, or null before it answers. */
		deletePreview: BulkDeleteItem[] | null;
		deletePreviewLoading: boolean;
		/** Preview rows that would be deleted. */
		deletable: BulkDeleteItem[];
		/** Preview rows that would be skipped. */
		blockedCount: number;
		deleting: boolean;
		deleteError: string | null;
		/** Display name for a preview row the server did not name. */
		nameOf: (strategyId: string) => string;
		oncancel: () => void;
		onconfirm: () => void;
	} = $props();
</script>

<ConfirmDialog
	open={deleteTargets.length > 0}
	title={deleteTargets.length === 1
		? `Delete ${deleteTargets[0]!.name}?`
		: `Delete ${deleteTargets.length} strategies?`}
	tone="danger"
	confirmLabel={deletable.length === 1
		? 'Delete 1 strategy'
		: `Delete ${deletable.length} strategies`}
	pendingLabel="Deleting…"
	pending={deleting}
	confirmDisabled={deletePreviewLoading || deletable.length === 0}
	confirmDisabledReason={deletePreviewLoading
		? 'Checking what would be deleted…'
		: deletePreview !== null && deletable.length === 0
			? 'Nothing selected can be deleted right now.'
			: null}
	error={deleteError}
	testId="delete-dialog"
	{oncancel}
	{onconfirm}
>
	<p>
		Deleting is permanent. Each strategy and its backtests, studies, research jobs, rules snapshots,
		and paper bots (with their orders and trade reasons) are removed.
	</p>
	<p class="kept">
		Live history is kept: stopped live bots keep their orders, fills, positions, trade reasons, and
		the rules they ran, and show as “(deleted strategy)”.
	</p>
	{#if deletePreviewLoading}
		<p class="faint" aria-busy="true">Checking what would be deleted…</p>
	{:else if deletePreview}
		<ul class="preview" aria-label="What will be deleted" data-testid="delete-preview">
			{#each deletePreview as item (item.strategy_id)}
				<li data-outcome={item.outcome}>
					<strong>{item.name ?? nameOf(item.strategy_id)}</strong>
					<span class:neg={item.outcome !== 'would_delete'}>{bulkOutcomeText(item)}</span>
					{#if item.outcome === 'would_delete' && item.counts}
						<span class="faint">{deletionCountsText(item.counts).join(' · ')}</span>
					{/if}
					{#if item.outcome === 'blocked' && item.deployment_ids.length > 0}
						<span class="faint"
							>Running or paused bots can't be deleted from under you: stop them (managed stop or
							flatten) first.
							{#each item.deployment_ids as deploymentId (deploymentId)}
								<a href={resolve(`/deployments/${encodeURIComponent(deploymentId)}`)}>Open bot</a>
							{/each}</span
						>
					{/if}
				</li>
			{/each}
		</ul>
		{#if blockedCount > 0 && deletable.length > 0}
			<p class="faint">
				{blockedCount} of {deletePreview.length} will be skipped; the rest are deleted.
			</p>
		{/if}
	{/if}
</ConfirmDialog>

<style>
	.faint {
		color: var(--faint);
	}
	.preview {
		display: grid;
		gap: 6px;
		margin: 8px 0 0;
		padding: 0;
		list-style: none;
	}
	.preview li {
		display: grid;
		gap: 2px;
	}
	.neg {
		color: var(--neg);
	}
	.kept {
		color: var(--muted);
	}
</style>
