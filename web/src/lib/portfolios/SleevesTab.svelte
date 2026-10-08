<script lang="ts">
	/**
	 * Sleeves tab: one row per sleeve (strategy, weight, market, capital, its bot
	 * and status, issues), per-sleeve Start / Pause / Resume / Stop, the strategy
	 * picker, the weight editor, and the allocation aside.
	 *
	 * Every change is revision-guarded. A 409 reloads the portfolio and says so;
	 * nothing is merged or retried silently.
	 *
	 * Each running bot lists its open books compactly (state, unrealized PnL at
	 * the last evaluated bar, time held, entry/stop/target; ADR 0098), and the
	 * "Paper vs live" panel below compares entry fills of sleeves with a twin.
	 *
	 * The table, allocation aside, and dialogs are `SleevesTable`,
	 * `AllocationAside`, `AddSleeveDialog`, and `RemoveSleeveDialog`; pure row
	 * helpers live in `sleeves.ts`. This tab owns the drafts and every change.
	 */
	import { onDestroy } from 'svelte';
	import type { Deployment } from '$lib/deployments';
	import { compareDecimalStrings } from '$lib/portfolio';
	import {
		CONFLICT_RELOADED,
		addSleeve,
		allocationBars,
		checkAllocation,
		errorText,
		fractionToPercentInput,
		isRevisionConflict,
		percentInputToFraction,
		quoteText,
		remainingWeight,
		removeSleeve,
		setWeights,
		weightPercent,
		type Portfolio,
		type PortfolioDeployment,
		type PortfolioDialogAction,
		type PortfolioSleeve
	} from '$lib/portfolios';
	import type { StrategyLibraryEntry } from '$lib/strategies';
	import type { PaperLiveFillComparison } from '$lib/fill-comparison';
	import AddSleeveDialog from './AddSleeveDialog.svelte';
	import AllocationAside from './AllocationAside.svelte';
	import PaperLiveFills from './PaperLiveFills.svelte';
	import RemoveSleeveDialog from './RemoveSleeveDialog.svelte';
	import SleevesTable from './SleevesTable.svelte';

	let {
		portfolio,
		deployment = null,
		strategies,
		strategiesError,
		inventory,
		onchanged,
		onconflict,
		onaction = () => {},
		fills = [],
		fillWarnings = []
	}: {
		portfolio: Portfolio;
		/** The portfolio's deployment view (sleeve bots), or null before it loads. */
		deployment?: PortfolioDeployment | null;
		strategies: StrategyLibraryEntry[] | null;
		strategiesError: string | null;
		inventory: Deployment[] | null;
		onchanged: (portfolio: Portfolio) => void;
		onconflict: () => Promise<void>;
		/** Open the confirmation for one sleeve's start, pause, resume, or stop. */
		onaction?: (action: PortfolioDialogAction, sleeveId: string) => void;
		/** Paper vs live twins of this portfolio's sleeves (ADR 0098). */
		fills?: PaperLiveFillComparison[];
		fillWarnings?: string[];
	} = $props();

	/** Clock for the open books' held time; a minute is fine-grained enough. */
	let now = $state(Date.now());
	const clock = setInterval(() => (now = Date.now()), 60_000);
	onDestroy(() => clearInterval(clock));

	let notice = $state<string | null>(null);

	let pickerOpen = $state(false);
	let query = $state('');
	let selected = $state<string | null>(null);
	let weight = $state('');
	let note = $state('');
	let pickerPending = $state(false);
	let pickerError = $state<string | null>(null);

	let editing = $state(false);
	let draft = $state<Record<string, string>>({});
	let reserveDraft = $state('');
	let savingWeights = $state(false);
	let weightsError = $state<string | null>(null);

	let removeTarget = $state<PortfolioSleeve | null>(null);
	let removing = $state(false);
	let removeError = $state<string | null>(null);

	const bars = $derived(allocationBars(portfolio));
	const fits = $derived(remainingWeight(portfolio));
	const weightFraction = $derived(percentInputToFraction(weight));
	const pickerProblem = $derived.by((): string | null => {
		if (selected === null) return 'Pick a strategy.';
		if (weightFraction === null || compareDecimalStrings(weightFraction, '0') <= 0)
			return 'Weight is a percent above 0 (up to 2 decimals).';
		if (compareDecimalStrings(weightFraction, fits) > 0)
			return `At most ${weightPercent(fits)} fits beside the other sleeves and the reserve.`;
		return null;
	});

	const draftFractions = $derived(
		portfolio.sleeves.map((sleeve) => percentInputToFraction(draft[sleeve.sleeve_id] ?? ''))
	);
	const reserveFraction = $derived(percentInputToFraction(reserveDraft));
	const draftProblem = $derived.by((): string | null => {
		if (draftFractions.some((value) => value === null || compareDecimalStrings(value, '0') <= 0))
			return 'Every weight is a percent above 0 (up to 2 decimals).';
		if (reserveFraction === null || compareDecimalStrings(reserveFraction, '1') >= 0)
			return 'Cash reserve is a percent from 0 to below 100.';
		const check = checkAllocation(
			draftFractions.filter((value): value is string => value !== null),
			reserveFraction
		);
		return check.ok
			? null
			: `Weights and reserve come to ${weightPercent(check.total)}; at most 100%.`;
	});
	const draftTotal = $derived.by((): string | null => {
		if (draftFractions.some((value) => value === null) || reserveFraction === null) return null;
		return checkAllocation(
			draftFractions.filter((value): value is string => value !== null),
			reserveFraction
		).total;
	});

	async function failure(caught: unknown, fallback: string): Promise<string> {
		if (isRevisionConflict(caught)) {
			await onconflict();
			return CONFLICT_RELOADED;
		}
		return errorText(caught, fallback);
	}

	function openPicker(): void {
		query = '';
		selected = null;
		weight = '';
		note = '';
		pickerError = null;
		notice = null;
		pickerOpen = true;
	}

	async function confirmAdd(): Promise<void> {
		if (pickerProblem !== null || selected === null || weightFraction === null) return;
		pickerPending = true;
		pickerError = null;
		try {
			const updated = await addSleeve(portfolio.portfolio_id, {
				revision: portfolio.revision,
				strategy_id: selected,
				weight_fraction: weightFraction,
				...(note.trim() === '' ? {} : { note: note.trim() })
			});
			onchanged(updated);
			pickerOpen = false;
		} catch (caught) {
			pickerError = await failure(caught, 'The sleeve could not be added.');
		} finally {
			pickerPending = false;
		}
	}

	function startEditing(): void {
		draft = Object.fromEntries(
			portfolio.sleeves.map((sleeve) => [
				sleeve.sleeve_id,
				fractionToPercentInput(sleeve.weight_fraction)
			])
		);
		reserveDraft = fractionToPercentInput(portfolio.cash_reserve_fraction);
		weightsError = null;
		notice = null;
		editing = true;
	}

	async function saveWeights(): Promise<void> {
		if (draftProblem !== null || reserveFraction === null) return;
		savingWeights = true;
		weightsError = null;
		try {
			const updated = await setWeights(portfolio.portfolio_id, {
				revision: portfolio.revision,
				weights: portfolio.sleeves.map((sleeve, index) => ({
					sleeve_id: sleeve.sleeve_id,
					weight_fraction: draftFractions[index] ?? sleeve.weight_fraction
				})),
				cash_reserve_fraction: reserveFraction
			});
			onchanged(updated);
			editing = false;
			notice = 'Weights saved.';
		} catch (caught) {
			weightsError = await failure(caught, 'The weights could not be saved.');
			if (weightsError === CONFLICT_RELOADED) editing = false;
		} finally {
			savingWeights = false;
		}
	}

	async function confirmRemove(): Promise<void> {
		const target = removeTarget;
		if (target === null) return;
		removing = true;
		removeError = null;
		try {
			const updated = await removeSleeve(
				portfolio.portfolio_id,
				target.sleeve_id,
				portfolio.revision
			);
			onchanged(updated);
			removeTarget = null;
			notice = `Removed “${target.strategy_name}”.`;
		} catch (caught) {
			removeError = await failure(caught, 'The sleeve could not be removed.');
		} finally {
			removing = false;
		}
	}
</script>

<div class="layout">
	<div class="main-col">
		<section class="card sleeves" aria-label="Sleeves">
			{#if portfolio.sleeves.length === 0}
				<div class="empty">
					<h2>No sleeves yet</h2>
					<p>
						Add a strategy from your library. Each sleeve gets its weight of the portfolio's
						capital; the rest stays in cash.
					</p>
				</div>
			{:else}
				<SleevesTable
					{portfolio}
					{deployment}
					{inventory}
					{editing}
					bind:draft
					{draftFractions}
					{bars}
					{now}
					{fills}
					{onaction}
					onremove={(sleeve) => {
						removeError = null;
						removeTarget = sleeve;
					}}
				/>
			{/if}
			<div class="foot">
				{#if editing}
					<label class="weight-input">
						<span>Cash reserve</span>
						<input
							type="text"
							inputmode="decimal"
							bind:value={reserveDraft}
							aria-label="Cash reserve (%)"
							aria-invalid={reserveFraction === null}
						/>
						<span class="faint">%</span>
					</label>
					<span class="total" data-testid="weights-total" class:over={draftProblem !== null}>
						{draftTotal === null ? 'Total —' : `Total ${weightPercent(draftTotal)} of capital`}
					</span>
					{#if draftProblem !== null}<span class="problem" role="status">{draftProblem}</span>{/if}
					<span class="spacer"></span>
					<button
						type="button"
						class="btn"
						onclick={() => (editing = false)}
						disabled={savingWeights}>Cancel</button
					>
					<button
						type="button"
						class="btn primary"
						disabled={draftProblem !== null || savingWeights}
						onclick={() => void saveWeights()}>{savingWeights ? 'Saving…' : 'Save weights'}</button
					>
				{:else}
					<button type="button" class="btn" onclick={openPicker}
						>+ Add sleeve from a strategy</button
					>
					{#if portfolio.sleeves.length > 0}
						<button type="button" class="btn ghost" onclick={startEditing}>Edit weights</button>
					{/if}
					<span class="spacer"></span>
					<span class="faint" data-testid="cash-reserve"
						>Cash reserve: {quoteText(
							portfolio.allocation.cash_reserve_quote,
							portfolio.quote_currency
						)}
						({weightPercent(portfolio.cash_reserve_fraction)})</span
					>
				{/if}
			</div>
			{#if weightsError}<p class="problem pad" role="alert">{weightsError}</p>{/if}
			{#if notice}<p class="notice pad" role="status">{notice}</p>{/if}
		</section>
		{#if fills.length > 0}
			<PaperLiveFills portfolioId={portfolio.portfolio_id} rows={fills} warnings={fillWarnings} />
		{/if}
	</div>

	<AllocationAside {portfolio} {bars} />
</div>

<AddSleeveDialog
	open={pickerOpen}
	{portfolio}
	{strategies}
	{strategiesError}
	{fits}
	bind:query
	bind:selected
	bind:weight
	bind:note
	pending={pickerPending}
	{pickerProblem}
	{pickerError}
	oncancel={() => (pickerOpen = false)}
	onconfirm={() => void confirmAdd()}
/>

<RemoveSleeveDialog
	{portfolio}
	{removeTarget}
	pending={removing}
	error={removeError}
	oncancel={() => (removeTarget = null)}
	onconfirm={() => void confirmRemove()}
/>

<style>
	.layout {
		display: grid;
		grid-template-columns: minmax(0, 1fr) 300px;
		gap: 16px;
		align-items: start;
	}
	.main-col {
		display: grid;
		gap: 16px;
		min-width: 0;
	}
	.sleeves {
		overflow: hidden;
	}
	.weight-input {
		display: inline-flex;
		align-items: center;
		gap: 6px;
		color: var(--muted);
		font-size: var(--fs-sm);
	}
	.weight-input input {
		width: 72px;
		min-height: 30px;
		padding: 0 8px;
		border: 1px solid var(--line-2);
		border-radius: var(--radius-sm);
		background: var(--surface-2);
		color: var(--text);
		text-align: right;
	}
	.weight-input input[aria-invalid='true'] {
		border-color: var(--danger-line);
	}
	.foot {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 8px;
		padding: 12px 16px;
		border-top: 1px solid var(--line);
	}
	.spacer {
		flex: 1;
	}
	.total {
		font-weight: 500;
	}
	.total.over,
	.problem {
		color: var(--neg);
	}
	.pad {
		margin: 0;
		padding: 0 16px 12px;
	}
	.notice {
		color: var(--muted);
	}
	.empty {
		display: grid;
		gap: 6px;
		padding: 24px;
		color: var(--muted);
	}
	.empty p {
		margin: 0;
		max-width: 60ch;
	}
	.faint {
		color: var(--faint);
	}
	@media (max-width: 1000px) {
		.layout {
			grid-template-columns: minmax(0, 1fr);
		}
	}
</style>
