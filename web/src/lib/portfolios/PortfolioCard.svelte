<script lang="ts">
	/**
	 * The selected portfolio's card: name, mode, deployment state and breaker
	 * chips, capital / allocation / equity metrics, Start / Pause / Resume /
	 * Stop, action notices, and the Sleeves · Portfolio backtest · Manager ·
	 * Limits tab list (arrow keys move between tabs).
	 */
	import {
		PORTFOLIO_TABS,
		deploymentStateLabel,
		modeLabel,
		portfolioSubtitle,
		quoteText,
		signedQuote,
		weightPercent,
		type Portfolio,
		type PortfolioDeployment,
		type PortfolioDialogAction,
		type PortfolioTab
	} from '$lib/portfolios';

	let {
		portfolio,
		view,
		actions,
		pnl,
		actionNotice,
		deploymentError,
		tab,
		onaction,
		onchoose
	}: {
		portfolio: Portfolio;
		/** This portfolio's deployment view, or null before it loads. */
		view: PortfolioDeployment | null;
		/** Which portfolio-wide actions make sense now. */
		actions: { start: boolean; pause: boolean; resume: boolean; stop: boolean };
		/** Net PnL of the current run, or null. */
		pnl: string | null;
		actionNotice: string | null;
		deploymentError: string | null;
		tab: PortfolioTab;
		/** Open the confirmation for one portfolio-wide action. */
		onaction: (action: PortfolioDialogAction) => void;
		onchoose: (tab: PortfolioTab) => void;
	} = $props();

	function onTabKey(event: KeyboardEvent, index: number): void {
		const delta = event.key === 'ArrowRight' ? 1 : event.key === 'ArrowLeft' ? -1 : 0;
		if (delta === 0) return;
		event.preventDefault();
		const target = PORTFOLIO_TABS[(index + delta + PORTFOLIO_TABS.length) % PORTFOLIO_TABS.length];
		onchoose(target.id);
		document.getElementById(`portfolio-tab-${target.id}`)?.focus();
	}
</script>

<section
	class="card idbar"
	class:live-card={portfolio.mode === 'live'}
	aria-label="Selected portfolio"
	data-testid="portfolio-card"
>
	<div class="identity">
		<div class="title-row">
			<span class="pf-name" data-testid="portfolio-name" title={portfolio.name}
				>{portfolio.name}</span
			>
			<span
				class="chip"
				class:live={portfolio.mode === 'live'}
				class:paper={portfolio.mode === 'paper'}>{modeLabel(portfolio.mode)}</span
			>
			<span class="chip" data-testid="portfolio-state" class:running={view?.state === 'running'}
				>{deploymentStateLabel(view?.state ?? portfolio.deployment_state ?? 'not_deployed')}</span
			>
			{#if view?.breaker.latched}
				<span class="chip breaker" data-testid="portfolio-breaker-chip">Breaker latched</span>
			{/if}
		</div>
		<div class="muted">{portfolioSubtitle(portfolio, view?.state ?? null)}</div>
	</div>
	<div class="metrics">
		<div class="metric">
			<div class="l">Capital</div>
			<div class="v">{quoteText(portfolio.capital_quote, portfolio.quote_currency)}</div>
		</div>
		<div class="metric">
			<div class="l">Allocated</div>
			<div class="v">{weightPercent(portfolio.allocation.allocated_fraction)}</div>
		</div>
		<div class="metric">
			<div class="l">Cash reserve</div>
			<div class="v">
				{quoteText(portfolio.allocation.cash_reserve_quote, portfolio.quote_currency)}
			</div>
		</div>
		<div class="metric">
			<div class="l">Sleeves</div>
			<div class="v">{portfolio.sleeves.length}</div>
		</div>
		{#if view !== null && view.state !== 'not_deployed'}
			<div class="metric" data-testid="portfolio-equity">
				<div class="l">Equity (this run)</div>
				<div class="v">
					{quoteText(view.breaker.equity, portfolio.quote_currency)}
					{#if pnl !== null}<span class="delta small" data-testid="portfolio-pnl"
							>{signedQuote(pnl, portfolio.quote_currency)}</span
						>{/if}
				</div>
			</div>
			<div class="metric">
				<div class="l">Exposure</div>
				<div class="v">{weightPercent(view.exposure.fraction_of_capital)}</div>
			</div>
		{/if}
		<div class="deploy" data-testid="portfolio-controls">
			<div class="deploy-buttons">
				<button
					type="button"
					class={view === null || view.state === 'not_deployed' ? 'btn primary' : 'btn'}
					data-testid="portfolio-start"
					disabled={!actions.start || !portfolio.deployable}
					onclick={() => onaction('start')}
					>{view?.state === 'not_deployed' || view === null
						? 'Start portfolio…'
						: 'Start stopped sleeves…'}</button
				>
				<button
					type="button"
					class="btn"
					data-testid="portfolio-pause"
					disabled={!actions.pause}
					onclick={() => onaction('pause')}>Pause all</button
				>
				<button
					type="button"
					class="btn"
					data-testid="portfolio-resume"
					disabled={!actions.resume}
					onclick={() => onaction('resume')}>Resume…</button
				>
				<button
					type="button"
					class="btn danger"
					data-testid="portfolio-stop"
					disabled={!actions.stop}
					onclick={() => onaction('stop')}>Stop…</button
				>
			</div>
			{#if !portfolio.deployable}
				<span class="faint small">Add sleeves and fix their issues before starting.</span>
			{:else if view?.breaker.latched}
				<span class="faint small">A breaker is latched: reset it on the Limits tab first.</span>
			{/if}
		</div>
	</div>
	{#if actionNotice}<p class="action-notice small" role="status">{actionNotice}</p>{/if}
	{#if deploymentError}<p class="problem small" role="alert">{deploymentError}</p>{/if}
	<div class="tabs" role="tablist" aria-label="Portfolio views">
		{#each PORTFOLIO_TABS as item, index (item.id)}
			<button
				type="button"
				role="tab"
				id="portfolio-tab-{item.id}"
				class="tab"
				class:on={tab === item.id}
				aria-selected={tab === item.id}
				aria-controls="portfolio-panel"
				tabindex={tab === item.id ? 0 : -1}
				onclick={() => onchoose(item.id)}
				onkeydown={(event) => onTabKey(event, index)}
			>
				{item.label}
				{#if item.id === 'sleeves'}<span class="n">{portfolio.sleeves.length}</span>{/if}
				{#if item.id === 'manager' && (view?.pending_proposals ?? 0) > 0}<span
						class="chip small-chip waiting"
						data-testid="manager-waiting">{view?.pending_proposals} waiting</span
					>{/if}
			</button>
		{/each}
	</div>
</section>

<style>
	.chip.running {
		border-color: var(--pos);
		color: var(--pos);
	}
	.chip.breaker {
		border-color: var(--neg);
		color: var(--neg);
	}
	.waiting {
		border-color: var(--accent);
		color: var(--accent);
	}
	.deploy-buttons {
		display: flex;
		flex-wrap: wrap;
		justify-content: flex-end;
		gap: 8px;
	}
	.delta {
		margin-left: 6px;
		color: var(--muted);
		font-weight: 500;
	}
	.action-notice {
		margin: 10px 0 0;
		color: var(--muted);
	}
	.problem {
		margin: 10px 0 0;
		color: var(--neg);
	}
	.idbar {
		padding: 16px 18px 0;
	}
	.live-card {
		border-color: var(--live-line);
	}
	.identity {
		min-width: 0;
	}
	.title-row {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 10px;
		min-width: 0;
	}
	.pf-name {
		min-width: 0;
		max-width: 100%;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		font-size: var(--fs-lg);
		font-weight: 600;
	}
	.metrics {
		display: flex;
		flex-wrap: wrap;
		align-items: flex-end;
		gap: 16px 28px;
		margin-top: 14px;
	}
	.metric .l {
		color: var(--muted);
		font-size: var(--fs-sm);
	}
	.metric .v {
		margin-top: 2px;
		font-size: var(--fs-xl);
		font-weight: 600;
		letter-spacing: -0.01em;
	}
	.deploy {
		display: grid;
		justify-items: end;
		gap: 4px;
		margin-left: auto;
	}
	.tabs {
		display: flex;
		flex-wrap: wrap;
		gap: 4px;
		margin-top: 14px;
		border-top: 1px solid var(--line);
	}
	.tab {
		display: inline-flex;
		align-items: center;
		gap: 6px;
		padding: 12px 12px 10px;
		border: 0;
		border-bottom: 2px solid transparent;
		background: transparent;
		color: var(--muted);
		font: inherit;
		font-weight: 500;
		cursor: pointer;
	}
	.tab:hover {
		color: var(--text);
	}
	.tab.on {
		border-bottom-color: var(--accent);
		color: var(--text);
	}
	.n {
		color: var(--faint);
		font-size: var(--fs-xs);
	}
	.small-chip {
		height: 18px;
		font-size: var(--fs-xs);
	}
	.muted {
		color: var(--muted);
	}
	.faint {
		color: var(--faint);
	}
	.small {
		font-size: var(--fs-sm);
	}
	@media (max-width: 720px) {
		.deploy {
			justify-items: start;
			margin-left: 0;
		}
	}
</style>
