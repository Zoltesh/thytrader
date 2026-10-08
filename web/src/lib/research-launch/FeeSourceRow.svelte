<script lang="ts">
	/**
	 * Where the modeled maker/taker rates came from (account suggestion,
	 * custom, or a stale suggestion), with a reload of the fee tier and a way
	 * back to the latest suggested rates.
	 */
	import {
		formatScheduleContext,
		type ResearchFeeFieldSource,
		type ResearchFeeSuggestion
	} from '$lib/fees';

	let {
		feeFieldSource,
		feeSourceChip,
		latestFeeSuggestion,
		onreload,
		onapply
	}: {
		feeFieldSource: ResearchFeeFieldSource;
		feeSourceChip: string;
		latestFeeSuggestion: ResearchFeeSuggestion | null;
		/** Re-read the Coinbase fee tier. */
		onreload: () => void;
		/** Put the suggestion's rates into the fee fields. */
		onapply: (suggestion: ResearchFeeSuggestion) => void;
	} = $props();
</script>

<div class="fee-source-row">
	<span
		class="fee-source-chip"
		class:custom={feeFieldSource === 'custom'}
		class:stale={feeFieldSource === 'stale-suggestion'}
		data-testid="research-fee-source"
		title={latestFeeSuggestion !== null ? formatScheduleContext(latestFeeSuggestion) : undefined}
		>{feeSourceChip}</span
	>
	<button class="secondary fee-source-action" type="button" onclick={() => onreload()}
		>Reload fee-tier</button
	>
	{#if latestFeeSuggestion !== null && feeFieldSource === 'stale-suggestion'}
		{@const suggestion = latestFeeSuggestion}
		<button class="secondary fee-source-action" type="button" onclick={() => onapply(suggestion)}
			>Refresh suggestion</button
		>
	{:else if latestFeeSuggestion !== null && feeFieldSource === 'custom'}
		{@const suggestion = latestFeeSuggestion}
		<button class="secondary fee-source-action" type="button" onclick={() => onapply(suggestion)}
			>Apply suggested rates</button
		>
	{/if}
</div>

<style>
	.fee-source-row {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 8px;
	}
	.fee-source-chip {
		display: inline-flex;
		align-items: center;
		border: 1px solid var(--line-2);
		border-radius: 999px;
		padding: 3px 10px;
		font-size: 11px;
		color: var(--muted);
		background: var(--surface);
	}
	.fee-source-chip.custom {
		color: var(--text);
		border-color: var(--line-strong);
	}
	.fee-source-chip.stale {
		color: var(--warn);
		border-color: var(--warn-line);
	}
	.fee-source-action {
		font-size: 12px;
		padding: 3px 10px;
	}
	.secondary {
		color: var(--text);
		background: var(--surface-2);
		border: 1px solid var(--line-2);
		border-radius: 9px;
		padding: 6px 10px;
		cursor: pointer;
	}
</style>
