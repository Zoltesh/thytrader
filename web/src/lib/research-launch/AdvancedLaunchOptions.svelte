<script lang="ts">
	/**
	 * "Advanced options" of the run bar: fixed slippage, optional spread
	 * stress, and the evaluation window (bounded by the selected dataset after
	 * warmup). The summary repeats the current values.
	 */
	import type { LaunchForm } from './launch-plan';

	let {
		launchForm = $bindable(),
		spreadStress,
		period,
		bounds,
		hint
	}: {
		launchForm: LaunchForm;
		/** The spread stress a launch would send, or null when none. */
		spreadStress: string | null;
		/** Period summary ("Full coverage", dates, or "Not set"). */
		period: string;
		/** The selected dataset's evaluable window, when one is selected. */
		bounds: { min: string; max: string } | null;
		/** Clock-aware note about the window, when a dataset is selected. */
		hint: string | null;
	} = $props();
</script>

<details class="advanced" data-testid="run-advanced">
	<summary
		>Advanced options <span class="summary-values"
			>slippage {launchForm.fixed_slippage_bps || '—'} bps{spreadStress !== null
				? ` · spread stress ${spreadStress} bps`
				: ''} · {period}</span
		></summary
	>
	<div class="advanced-body">
		<div class="launch-grid">
			<label
				>Fixed slippage (bps)
				<input inputmode="decimal" bind:value={launchForm.fixed_slippage_bps} /></label
			>
			<label
				>Spread stress (bps, total bid-ask, optional)
				<input inputmode="decimal" placeholder="0" bind:value={launchForm.spread_bps} /></label
			>
		</div>
		<div class="launch-grid">
			<label
				>Evaluation start
				<input
					type="datetime-local"
					bind:value={launchForm.evaluation_start}
					min={bounds?.min}
					max={bounds?.max}
				/></label
			>
			<label
				>Evaluation end
				<input
					type="datetime-local"
					bind:value={launchForm.evaluation_end}
					min={bounds?.min}
					max={bounds?.max}
				/></label
			>
		</div>
		{#if hint !== null}
			<p class="view-note">{hint}</p>
		{/if}
	</div>
</details>

<style>
	.advanced summary {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 8px;
		color: var(--muted);
		font-size: var(--fs-sm);
		cursor: pointer;
	}
	.summary-values {
		color: var(--faint);
	}
	.advanced-body {
		display: grid;
		gap: 10px;
		margin-top: 10px;
		padding: 12px;
		border: 1px solid var(--line);
		border-radius: var(--radius-md);
		background: var(--surface-2);
	}
	.view-note {
		margin: 0;
		color: var(--muted);
		font-size: var(--fs-sm);
	}
	.launch-grid {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
		gap: 10px;
	}
	.launch-grid label {
		display: grid;
		gap: 4px;
		color: var(--muted);
		font-size: var(--fs-sm);
	}
	.launch-grid input {
		width: 100%;
		min-height: 34px;
		padding: 6px 9px;
		border: 1px solid var(--line-2);
		border-radius: var(--radius-md);
		background: var(--surface);
		color: var(--text);
		font: inherit;
		font-size: var(--fs-sm);
	}
</style>
