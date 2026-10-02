<script lang="ts">
	/**
	 * Sleeves tab: one row per sleeve (strategy, weight, market, capital, its bot
	 * and status, issues), per-sleeve Start / Pause / Resume / Stop, the strategy
	 * picker, the weight editor, and the allocation aside.
	 *
	 * Every change is revision-guarded. A 409 reloads the portfolio and says so;
	 * nothing is merged or retried silently.
	 */
	import { resolve } from '$app/paths';
	import ConfirmDialog from '$lib/ConfirmDialog.svelte';
	import { marketLabel } from '$lib/deployment-detail';
	import type { Deployment } from '$lib/deployments';
	import { compareDecimalStrings } from '$lib/portfolio';
	import {
		CONFLICT_RELOADED,
		addSleeve,
		allocationAriaLabel,
		allocationBars,
		checkAllocation,
		errorText,
		fractionToPercentInput,
		isRevisionConflict,
		largestAssetText,
		percentInputToFraction,
		pickerOptions,
		quoteText,
		remainingWeight,
		botStatusText,
		pausedByBreaker,
		removeSleeve,
		setWeights,
		signedQuote,
		sleeveBots,
		sleeveIssueText,
		sleevePositionText,
		weightPercent,
		type Portfolio,
		type PortfolioDeployment,
		type PortfolioDialogAction,
		type PortfolioSleeve,
		type SleeveDeployment
	} from '$lib/portfolios';
	import type { StrategyLibraryEntry } from '$lib/strategies';

	let {
		portfolio,
		deployment = null,
		strategies,
		strategiesError,
		inventory,
		onchanged,
		onconflict,
		onaction = () => {}
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
	} = $props();

	/** The sleeve's portfolio bot (running, paused, or its last stopped one). */
	function sleeveBot(sleeveId: string): SleeveDeployment | null {
		return deployment?.sleeves.find((book) => book.sleeve_id === sleeveId)?.deployment ?? null;
	}

	function occupied(bot: SleeveDeployment | null): boolean {
		return bot !== null && (bot.status === 'running' || bot.status === 'paused');
	}

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
	const largest = $derived(largestAssetText(portfolio));
	const options = $derived(strategies === null ? [] : pickerOptions(strategies, portfolio, query));
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

	function botLabel(deployment: Deployment): string {
		const status = deployment.status;
		return `${status.charAt(0).toUpperCase()}${status.slice(1)} bot`;
	}
</script>

<div class="layout">
	<section class="card sleeves" aria-label="Sleeves">
		{#if portfolio.sleeves.length === 0}
			<div class="empty">
				<h2>No sleeves yet</h2>
				<p>
					Add a strategy from your library. Each sleeve gets its weight of the portfolio's capital;
					the rest stays in cash.
				</p>
			</div>
		{:else}
			<div class="table-wrap">
				<table>
					<thead>
						<tr>
							<th scope="col">Sleeve</th>
							<th scope="col" class="left">Weight</th>
							<th scope="col" class="left">Market</th>
							<th scope="col">Capital</th>
							<th scope="col" class="left">Bot ({portfolio.mode === 'live' ? 'live' : 'paper'})</th>
							<th scope="col" class="left">Status</th>
							<th scope="col"><span class="sr-only">Actions</span></th>
						</tr>
					</thead>
					<tbody>
						{#each portfolio.sleeves as sleeve, index (sleeve.sleeve_id)}
							{@const bots = sleeveBots(inventory, sleeve.strategy_id, portfolio.mode)}
							{@const bot = sleeveBot(sleeve.sleeve_id)}
							{@const others = bots.filter((item) => item.id !== bot?.deployment_id)}
							<tr data-testid="sleeve-row">
								<td>
									<a
										class="name"
										href={resolve(`/strategies/${encodeURIComponent(sleeve.strategy_id)}`)}
										>{sleeve.strategy_name}</a
									>
									{#if sleeve.note}<div class="faint small">{sleeve.note}</div>{/if}
								</td>
								<td class="left">
									{#if editing}
										<label class="weight-input">
											<span class="sr-only">Weight for {sleeve.strategy_name} (%)</span>
											<input
												type="text"
												inputmode="decimal"
												bind:value={draft[sleeve.sleeve_id]}
												aria-invalid={draftFractions[index] === null}
											/>
											<span class="faint">%</span>
										</label>
									{:else}
										<div class="weight">{weightPercent(sleeve.weight_fraction)}</div>
										<div class="mini" aria-hidden="true">
											<div class="mini-fill" style:width={bars[index]?.width ?? '0%'}></div>
										</div>
									{/if}
								</td>
								<td class="left">
									{sleeve.product_id === null ? 'Unknown market' : marketLabel(sleeve.product_id)}
									<span class="faint">· {sleeve.timeframe ?? '—'}</span>
									{#if sleeve.covered_product_ids.length > 1}
										<div class="faint small">
											+{sleeve.covered_product_ids.length - 1} more product{sleeve
												.covered_product_ids.length === 2
												? ''
												: 's'}
										</div>
									{/if}
								</td>
								<td>{quoteText(sleeve.capital_quote, portfolio.quote_currency)}</td>
								<td class="left" data-testid="sleeve-bot">
									{#if bot !== null}
										<a
											class="bot-link"
											class:breaker={pausedByBreaker(bot)}
											href={resolve(`/deployments/${encodeURIComponent(bot.deployment_id)}`)}
											title={bot.mismatch_detail ?? undefined}>{botStatusText(bot)}</a
										>
										{@const positionState = sleevePositionText(bot)}
										{#if positionState !== null}
											<div class="faint small" data-testid="sleeve-position-state">
												{positionState}
											</div>
										{/if}
										{#if occupied(bot)}
											<div class="faint small">
												PnL {signedQuote(bot.net_pnl, portfolio.quote_currency)} · {quoteText(
													bot.allocated_capital ?? sleeve.capital_quote,
													portfolio.quote_currency
												)}
											</div>
										{/if}
									{:else if deployment !== null}
										<span class="faint">Not started</span>
									{/if}
									{#each others as other (other.id)}
										<a
											class="bot-link other"
											href={resolve(`/deployments/${encodeURIComponent(other.id)}`)}
											>{botLabel(other)} (outside this portfolio)</a
										>
									{/each}
									{#if bot === null && deployment === null && inventory !== null && bots.length === 0}
										<span class="faint">No bot</span>
									{/if}
								</td>
								<td class="left">
									{#if sleeve.issues.length === 0}
										<span class="muted">Ready to backtest</span>
									{:else}
										{#each sleeve.issues as issue (issue)}
											<div class="issue" data-testid="sleeve-issue">{sleeveIssueText(issue)}</div>
										{/each}
									{/if}
								</td>
								<td class="row-actions">
									{#if bot !== null && bot.status === 'running'}
										<button
											type="button"
											class="btn ghost compact"
											aria-label="Pause sleeve {sleeve.strategy_name}"
											onclick={() => onaction('pause', sleeve.sleeve_id)}>Pause</button
										>
									{:else if bot !== null && bot.status === 'paused'}
										<button
											type="button"
											class="btn ghost compact"
											aria-label="Resume sleeve {sleeve.strategy_name}"
											disabled={deployment?.breaker.latched === true}
											onclick={() => onaction('resume', sleeve.sleeve_id)}>Resume…</button
										>
									{:else if deployment !== null && sleeve.issues.length === 0}
										<button
											type="button"
											class="btn ghost compact"
											aria-label="Start sleeve {sleeve.strategy_name}"
											disabled={deployment.breaker.latched}
											onclick={() => onaction('start', sleeve.sleeve_id)}>Start…</button
										>
									{/if}
									{#if occupied(bot)}
										<button
											type="button"
											class="btn ghost compact"
											aria-label="Stop sleeve {sleeve.strategy_name}"
											onclick={() => onaction('stop', sleeve.sleeve_id)}>Stop…</button
										>
									{:else}
										<button
											type="button"
											class="btn ghost compact"
											aria-label="Remove sleeve {sleeve.strategy_name}"
											disabled={editing}
											onclick={() => {
												removeError = null;
												removeTarget = sleeve;
											}}>Remove…</button
										>
									{/if}
								</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
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
				<button type="button" class="btn" onclick={() => (editing = false)} disabled={savingWeights}
					>Cancel</button
				>
				<button
					type="button"
					class="btn primary"
					disabled={draftProblem !== null || savingWeights}
					onclick={() => void saveWeights()}>{savingWeights ? 'Saving…' : 'Save weights'}</button
				>
			{:else}
				<button type="button" class="btn" onclick={openPicker}>+ Add sleeve from a strategy</button>
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

	<aside class="card aside" aria-label="Allocation">
		<h2>Allocation</h2>
		<div
			class="bars"
			role="img"
			aria-label={allocationAriaLabel(bars)}
			data-testid="allocation-bars"
		>
			{#each bars as bar (bar.key)}
				<div class="bar">
					<div class="bar-label">
						<span class="muted">{bar.label}</span><span class="pct">{bar.percent}</span>
					</div>
					<div class="track">
						<div class="fill {bar.tone}" style:width={bar.width}></div>
					</div>
				</div>
			{/each}
		</div>
		<div class="check">
			<span class="muted">Largest single asset</span>
			<span data-testid="largest-asset" class:warn={largest.over}>{largest.text}</span>
		</div>
		<div class="check">
			<span class="muted">Allocated to sleeves</span>
			<span
				>{quoteText(portfolio.allocation.allocated_quote, portfolio.quote_currency)} ({weightPercent(
					portfolio.allocation.allocated_fraction
				)})</span
			>
		</div>
		{#if largest.over}
			<p class="warn small">
				The largest asset is above this portfolio's per-asset limit. Limits bind orders only once
				portfolio deployment arrives.
			</p>
		{/if}
	</aside>
</div>

<ConfirmDialog
	open={pickerOpen}
	title="Add a sleeve"
	confirmLabel="Add sleeve"
	pendingLabel="Adding…"
	pending={pickerPending}
	confirmDisabled={pickerProblem !== null}
	confirmDisabledReason={pickerProblem}
	error={pickerError}
	testId="sleeve-picker"
	oncancel={() => (pickerOpen = false)}
	onconfirm={() => void confirmAdd()}
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

<ConfirmDialog
	open={removeTarget !== null}
	title="Remove sleeve?"
	tone="danger"
	confirmLabel="Remove sleeve"
	pendingLabel="Removing…"
	pending={removing}
	error={removeError}
	testId="remove-sleeve-dialog"
	oncancel={() => (removeTarget = null)}
	onconfirm={() => void confirmRemove()}
>
	{#if removeTarget !== null}
		<p>
			Removes “{removeTarget.strategy_name}” ({weightPercent(removeTarget.weight_fraction)}) from {portfolio.name}.
			The strategy and its backtests stay in your library; the journal records the removal.
		</p>
	{/if}
</ConfirmDialog>

<style>
	.layout {
		display: grid;
		grid-template-columns: minmax(0, 1fr) 300px;
		gap: 16px;
		align-items: start;
	}
	.sleeves {
		overflow: hidden;
	}
	th.left,
	td.left {
		text-align: left;
	}
	th {
		white-space: nowrap;
	}
	.name {
		color: var(--text);
		font-weight: 500;
		text-decoration: none;
	}
	.name:hover {
		text-decoration: underline;
	}
	.weight {
		font-weight: 500;
	}
	.mini {
		width: 64px;
		height: 4px;
		margin-top: 6px;
		border-radius: 2px;
		background: var(--line);
	}
	.mini-fill {
		height: 4px;
		border-radius: 2px;
		background: var(--accent);
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
	.bot-link {
		display: block;
		color: var(--accent);
		text-decoration: none;
	}
	.bot-link.breaker {
		color: var(--neg);
	}
	.bot-link.other {
		color: var(--muted);
		font-size: var(--fs-sm);
	}
	.row-actions {
		white-space: nowrap;
	}
	.row-actions .btn + .btn {
		margin-left: 4px;
	}
	.issue {
		color: var(--warn);
		font-size: var(--fs-sm);
	}
	.compact {
		min-height: 28px;
		padding: 0 8px;
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
	.aside {
		padding: 16px 18px;
	}
	.bars {
		display: flex;
		flex-direction: column;
		gap: 12px;
		margin: 14px 0 10px;
	}
	.bar-label {
		display: flex;
		gap: 8px;
		font-size: var(--fs-sm);
	}
	.pct {
		margin-left: auto;
		font-weight: 500;
	}
	.track {
		height: 6px;
		margin-top: 5px;
		border-radius: 3px;
		background: var(--surface-2);
	}
	.fill {
		height: 6px;
		border-radius: 3px;
	}
	.fill.sleeve {
		background: var(--accent);
	}
	.fill.reserve {
		background: var(--line-strong);
	}
	.fill.unallocated {
		background: var(--line-2);
	}
	.check {
		display: flex;
		gap: 10px;
		padding: 8px 0;
		border-top: 1px solid var(--line);
		font-size: var(--fs-sm);
	}
	.check > span:first-child {
		flex: 1;
	}
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
	.muted {
		color: var(--muted);
	}
	.small {
		font-size: var(--fs-sm);
	}
	.warn {
		color: var(--warn);
	}
	@media (max-width: 1000px) {
		.layout {
			grid-template-columns: minmax(0, 1fr);
		}
	}
</style>
