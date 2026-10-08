<script lang="ts">
	/** Bar shown while strategies on the current page are selected: clear, or delete them. */
	let {
		count,
		disabled,
		onclear,
		ondelete
	}: {
		/** Selected strategies on the current page. */
		count: number;
		/** An action or deletion in flight. */
		disabled: boolean;
		onclear: () => void;
		/** Open the delete confirmation for the selection. */
		ondelete: () => void;
	} = $props();
</script>

<div class="bulk-bar" role="region" aria-label="Selected strategies" data-testid="bulk-bar">
	<span>{count} selected</span>
	<button class="btn ghost small" type="button" onclick={() => onclear()}>Clear selection</button>
	<button class="btn danger" type="button" {disabled} onclick={() => ondelete()}
		>Delete {count} strateg{count === 1 ? 'y' : 'ies'}…</button
	>
</div>

<style>
	.small {
		display: block;
		font-size: var(--fs-xs);
	}
	.btn.small {
		min-height: 28px;
		padding: 0 8px;
		font-size: var(--fs-sm);
	}
	.bulk-bar {
		display: flex;
		align-items: center;
		gap: 12px;
		margin-bottom: var(--space-3);
		padding: 10px 14px;
		border: 1px solid var(--line-2);
		border-radius: var(--radius-md);
		background: var(--surface-2);
	}
	.bulk-bar span {
		margin-right: auto;
	}
</style>
