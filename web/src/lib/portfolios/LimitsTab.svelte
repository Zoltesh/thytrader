<script lang="ts">
	/**
	 * Limits tab: the portfolio's shared limits, viewed and edited.
	 *
	 * They are stored now and bind orders only once portfolio deployment
	 * arrives; the copy says so rather than implying enforcement.
	 */
	import { compareDecimalStrings } from '$lib/portfolio';
	import {
		CONFLICT_RELOADED,
		LIMITS_NOTE,
		errorText,
		fractionToPercentInput,
		isRevisionConflict,
		multiplyDecimals,
		percentInputToFraction,
		quoteInput,
		quoteText,
		updatePortfolio,
		weightPercent,
		type Portfolio,
		type PortfolioLimits
	} from '$lib/portfolios';

	let {
		portfolio,
		onchanged,
		onconflict
	}: {
		portfolio: Portfolio;
		onchanged: (portfolio: Portfolio) => void;
		onconflict: () => Promise<void>;
	} = $props();

	let editing = $state(false);
	let exposure = $state('');
	let perAsset = $state('');
	let dailyLoss = $state('');
	let drawdown = $state('');
	let saving = $state(false);
	let error = $state<string | null>(null);
	let notice = $state<string | null>(null);

	const limits = $derived(portfolio.limits);

	function percentIn(value: string, { below100 }: { below100: boolean }): string | null {
		const fraction = percentInputToFraction(value);
		if (fraction === null || compareDecimalStrings(fraction, '0') <= 0) return null;
		const cap = compareDecimalStrings(fraction, '1');
		return below100 ? (cap < 0 ? fraction : null) : cap <= 0 ? fraction : null;
	}

	const draft = $derived.by((): { limits: PortfolioLimits | null; problem: string | null } => {
		const total = percentIn(exposure, { below100: false });
		if (total === null)
			return { limits: null, problem: 'Max total exposure is a percent above 0, at most 100.' };
		const asset = percentIn(perAsset, { below100: false });
		if (asset === null)
			return { limits: null, problem: 'Max per asset is a percent above 0, at most 100.' };
		const loss = dailyLoss.trim() === '' ? null : quoteInput(dailyLoss);
		if (dailyLoss.trim() !== '' && loss === null)
			return { limits: null, problem: 'Daily loss stop is a positive amount, or blank for none.' };
		const stop = drawdown.trim() === '' ? null : percentIn(drawdown, { below100: true });
		if (drawdown.trim() !== '' && stop === null)
			return { limits: null, problem: 'Max drawdown stop is a percent above 0, below 100.' };
		return {
			limits: {
				max_total_exposure_fraction: total,
				max_per_asset_fraction: asset,
				daily_loss_quote: loss,
				max_drawdown_fraction: stop
			},
			problem: null
		};
	});

	function startEditing(): void {
		exposure = fractionToPercentInput(limits.max_total_exposure_fraction);
		perAsset = fractionToPercentInput(limits.max_per_asset_fraction);
		dailyLoss = limits.daily_loss_quote ?? '';
		drawdown =
			limits.max_drawdown_fraction === null
				? ''
				: fractionToPercentInput(limits.max_drawdown_fraction);
		error = null;
		notice = null;
		editing = true;
	}

	async function save(): Promise<void> {
		const next = draft.limits;
		if (next === null) return;
		saving = true;
		error = null;
		try {
			const updated = await updatePortfolio(portfolio.portfolio_id, {
				revision: portfolio.revision,
				limits: next
			});
			onchanged(updated);
			editing = false;
			notice = 'Limits saved.';
		} catch (caught) {
			if (isRevisionConflict(caught)) {
				await onconflict();
				editing = false;
				error = CONFLICT_RELOADED;
			} else {
				error = errorText(caught, 'The limits could not be saved.');
			}
		} finally {
			saving = false;
		}
	}

	function quoteOf(fraction: string): string {
		return quoteText(multiplyDecimals(fraction, portfolio.capital_quote), portfolio.quote_currency);
	}
</script>

<div class="layout">
	<section class="card body" aria-label="Portfolio limits">
		<div class="head">
			<h2>Portfolio limits</h2>
			{#if !editing}
				<button type="button" class="btn ghost compact" onclick={startEditing}>Edit limits</button>
			{/if}
		</div>
		{#if editing}
			<div class="form">
				<label class="field">
					<span>Max total exposure (% of capital)</span>
					<input type="text" inputmode="decimal" bind:value={exposure} />
				</label>
				<label class="field">
					<span>Max per asset (% of capital)</span>
					<input type="text" inputmode="decimal" bind:value={perAsset} />
				</label>
				<label class="field">
					<span>Daily loss stop ({portfolio.quote_currency}, optional)</span>
					<input type="text" inputmode="decimal" bind:value={dailyLoss} placeholder="None" />
				</label>
				<label class="field">
					<span>Max drawdown stop (% from peak, optional)</span>
					<input type="text" inputmode="decimal" bind:value={drawdown} placeholder="None" />
				</label>
			</div>
			<div class="actions">
				{#if draft.problem}<span class="problem small" role="status">{draft.problem}</span>{/if}
				<span class="spacer"></span>
				<button type="button" class="btn" onclick={() => (editing = false)} disabled={saving}
					>Cancel</button
				>
				<button
					type="button"
					class="btn primary"
					disabled={draft.limits === null || saving}
					onclick={() => void save()}>{saving ? 'Saving…' : 'Save limits'}</button
				>
			</div>
		{:else}
			<div class="check" data-testid="limit-exposure">
				<span class="muted">Max total exposure</span>
				{weightPercent(limits.max_total_exposure_fraction)} of capital ({quoteOf(
					limits.max_total_exposure_fraction
				)})
			</div>
			<div class="check" data-testid="limit-per-asset">
				<span class="muted">Max per asset</span>
				{weightPercent(limits.max_per_asset_fraction)} of capital
			</div>
			<div class="check" data-testid="limit-daily-loss">
				<span class="muted">Daily loss stop</span>
				{limits.daily_loss_quote === null
					? 'None'
					: quoteText(limits.daily_loss_quote, portfolio.quote_currency)}
			</div>
			<div class="check" data-testid="limit-drawdown">
				<span class="muted">Max drawdown stop</span>
				{limits.max_drawdown_fraction === null
					? 'None'
					: `${weightPercent(limits.max_drawdown_fraction)} from peak`}
			</div>
			<div class="check">
				<span class="muted">Cash reserve (never deployed)</span>
				{quoteText(portfolio.allocation.cash_reserve_quote, portfolio.quote_currency)} ({weightPercent(
					portfolio.cash_reserve_fraction
				)})
			</div>
		{/if}
		{#if error}<p class="problem small" role="alert">{error}</p>{/if}
		{#if notice}<p class="muted small" role="status">{notice}</p>{/if}
	</section>
	<section class="card body" aria-label="Where limits apply">
		<h2>Where limits apply</h2>
		<p class="muted" data-testid="limits-note">{LIMITS_NOTE}</p>
		<p class="muted">
			Once deployment arrives, every order from every sleeve will pass the sleeve's own risk checks,
			then these portfolio limits, then the account-wide risk policy. The strictest limit wins.
		</p>
	</section>
</div>

<style>
	.layout {
		display: grid;
		grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
		gap: 16px;
		align-items: start;
	}
	.body {
		padding: 16px 18px;
	}
	.head {
		display: flex;
		align-items: center;
		gap: 8px;
		margin-bottom: 8px;
	}
	.head .btn {
		margin-left: auto;
	}
	.compact {
		min-height: 28px;
		padding: 0 8px;
	}
	.check {
		display: flex;
		gap: 10px;
		padding: 8px 0;
		border-top: 1px solid var(--line);
	}
	.check > span:first-child {
		flex: 1;
	}
	.form {
		display: grid;
		gap: 10px;
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
	.actions {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 8px;
		margin-top: 12px;
	}
	.spacer {
		flex: 1;
	}
	.muted {
		color: var(--muted);
	}
	.small {
		font-size: var(--fs-sm);
	}
	.problem {
		color: var(--neg);
	}
	p {
		margin: 8px 0 0;
	}
	@media (max-width: 900px) {
		.layout {
			grid-template-columns: minmax(0, 1fr);
		}
	}
</style>
