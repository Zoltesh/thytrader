<script lang="ts">
	/**
	 * Add-a-sleeve dialog: search the strategy library, pick one strategy (taken
	 * or mismatched ones are disabled with the reason), and give its weight and an
	 * optional note. The tab owns the draft and adds the sleeve.
	 */
	import ConfirmDialog from '$lib/ConfirmDialog.svelte';
	import { marketLabel } from '$lib/deployment-detail';
	import { pickerOptions, weightPercent, type Portfolio } from '$lib/portfolios';
	import type { StrategyLibraryEntry } from '$lib/strategies';

	let {
		open,
		portfolio,
		strategies,
		strategiesError,
		fits,
		query = $bindable(),
		selected = $bindable(),
		weight = $bindable(),
		note = $bindable(),
		pending,
		pickerProblem,
		pickerError,
		oncancel,
		onconfirm
	}: {
		open: boolean;
		portfolio: Portfolio;
		/** The strategy library, or null while it loads or after it failed. */
		strategies: StrategyLibraryEntry[] | null;
		strategiesError: string | null;
		/** Largest weight fraction that fits beside the other sleeves and the reserve. */
		fits: string;
		query: string;
		/** The picked strategy id. */
		selected: string | null;
		/** Weight as a percent input. */
		weight: string;
		note: string;
		pending: boolean;
		/** Why Add sleeve is disabled, or null. */
		pickerProblem: string | null;
		pickerError: string | null;
		oncancel: () => void;
		onconfirm: () => void;
	} = $props();

	const options = $derived(strategies === null ? [] : pickerOptions(strategies, portfolio, query));
</script>

<ConfirmDialog
	{open}
	title="Add a sleeve"
	confirmLabel="Add sleeve"
	pendingLabel="Adding…"
	{pending}
	confirmDisabled={pickerProblem !== null}
	confirmDisabledReason={pickerProblem}
	error={pickerError}
	testId="sleeve-picker"
	{oncancel}
	{onconfirm}
>
	<p>
		Pick a strategy quoted in {portfolio.quote_currency}. A strategy appears at most once per
		portfolio. Up to {weightPercent(fits)} of capital is free.
	</p>
	<label class="field">
		<span>Search strategies</span>
		<input type="search" bind:value={query} placeholder="Name or market" autocomplete="off" />
	</label>
	{#if strategies === null}
		<p class="faint">{strategiesError ?? 'Loading your strategy library…'}</p>
	{:else if options.length === 0}
		<p class="faint">No strategies match.</p>
	{:else}
		<div class="options" role="radiogroup" aria-label="Strategies">
			{#each options as option (option.entry.strategy_id)}
				<label class="option" class:disabled={option.disabledReason !== null}>
					<input
						type="radio"
						name="sleeve-strategy"
						value={option.entry.strategy_id}
						disabled={option.disabledReason !== null}
						checked={selected === option.entry.strategy_id}
						onchange={() => (selected = option.entry.strategy_id)}
					/>
					<span class="option-text">
						<span class="option-name">{option.entry.name}</span>
						<span class="faint small"
							>{option.entry.product_id === null
								? 'Unknown market'
								: marketLabel(option.entry.product_id)} · {option.entry.timeframe ?? '—'}</span
						>
						{#if option.disabledReason}
							<span class="faint small">{option.disabledReason}</span>
						{:else if option.warning}
							<span class="warn small">{option.warning}</span>
						{/if}
					</span>
				</label>
			{/each}
		</div>
	{/if}
	<div class="row-fields">
		<label class="field">
			<span>Weight (% of capital)</span>
			<input type="text" inputmode="decimal" bind:value={weight} placeholder="25" />
		</label>
		<label class="field">
			<span>Note (optional)</span>
			<input type="text" bind:value={note} maxlength="280" placeholder="Why this sleeve" />
		</label>
	</div>
</ConfirmDialog>

<style>
	.field {
		display: grid;
		gap: 6px;
		color: var(--muted);
		font-size: var(--fs-sm);
	}
	.field input {
		min-height: 34px;
		padding: 0 10px;
		border: 1px solid var(--line-2);
		border-radius: var(--radius-md);
		background: var(--surface-2);
		color: var(--text);
	}
	.options {
		display: grid;
		gap: 6px;
		max-height: 240px;
		overflow-y: auto;
		padding-right: 4px;
	}
	.option {
		display: flex;
		align-items: flex-start;
		gap: 10px;
		padding: 8px 10px;
		border: 1px solid var(--line);
		border-radius: var(--radius-md);
		color: var(--text);
		cursor: pointer;
	}
	.option.disabled {
		cursor: not-allowed;
		opacity: 0.6;
	}
	.option-text {
		display: grid;
		gap: 2px;
	}
	.option-name {
		font-weight: 500;
	}
	.row-fields {
		display: grid;
		grid-template-columns: 1fr 2fr;
		gap: 10px;
	}
	.faint {
		color: var(--faint);
	}
	.small {
		font-size: var(--fs-sm);
	}
	.warn {
		color: var(--warn);
	}
</style>
