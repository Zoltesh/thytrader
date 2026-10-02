<script lang="ts">
	/**
	 * Confirm a portfolio action (ADR 0091): start, pause, resume, or stop every
	 * sleeve (or one), or reset a latched portfolio breaker. Built on the shared
	 * `ConfirmDialog` (native modal, Cancel focused, Escape closes before submit).
	 *
	 * Live start and live resume re-arm real orders: the confirm stays disabled
	 * until the "real orders" checkbox is ticked, and `onconfirm` reports the
	 * acknowledgement so the caller sends `i_understand_live: true` only then.
	 */
	import ConfirmDialog from '$lib/ConfirmDialog.svelte';
	import {
		START_NOTE,
		breakerLabel,
		modeLabel,
		quoteText,
		weightPercent,
		type Portfolio,
		type PortfolioDeployment,
		type PortfolioDialogAction
	} from '$lib/portfolios';

	let {
		portfolio,
		deployment,
		action,
		sleeveId = null,
		stopWithFlatten = $bindable(false),
		pending,
		error = null,
		oncancel,
		onconfirm
	}: {
		portfolio: Portfolio;
		deployment: PortfolioDeployment | null;
		/** The dialog is open while an action is set. */
		action: PortfolioDialogAction | null;
		/** One sleeve, or null for the whole portfolio. */
		sleeveId?: string | null;
		stopWithFlatten: boolean;
		pending: boolean;
		error?: string | null;
		oncancel: () => void;
		onconfirm: (options: { liveAcknowledged: boolean }) => void;
	} = $props();

	const live = $derived(portfolio.mode === 'live');
	const needsAck = $derived(live && (action === 'start' || action === 'resume'));
	const sleeves = $derived(
		sleeveId === null
			? portfolio.sleeves
			: portfolio.sleeves.filter((sleeve) => sleeve.sleeve_id === sleeveId)
	);
	const target = $derived(
		sleeveId === null
			? `portfolio “${portfolio.name}”`
			: `sleeve “${sleeves[0]?.strategy_name ?? ''}”`
	);
	let liveAcknowledged = $state(false);

	$effect(() => {
		// Every opening starts unacknowledged; nothing carries over between dialogs.
		if (action === null) liveAcknowledged = false;
	});

	const titles: Record<PortfolioDialogAction, string> = {
		start: 'Start',
		pause: 'Pause',
		resume: 'Resume',
		stop: 'Stop',
		reset: 'Reset the breaker of'
	};
	const confirmLabels: Record<PortfolioDialogAction, string> = {
		start: 'Start',
		pause: 'Pause',
		resume: 'Resume',
		stop: 'Stop',
		reset: 'Reset breaker'
	};
	const title = $derived(action === null ? '' : `${titles[action]} ${target}?`);
	const confirmLabel = $derived(
		action === 'stop' && stopWithFlatten
			? 'Stop and flatten'
			: action === null
				? ''
				: live && (action === 'start' || action === 'resume')
					? `${confirmLabels[action]} live`
					: confirmLabels[action]
	);
	const tone = $derived(
		needsAck ? 'live' : action === 'stop' || action === 'reset' ? 'danger' : 'default'
	);
</script>

<ConfirmDialog
	open={action !== null}
	{title}
	{tone}
	liveChip={live}
	{confirmLabel}
	pendingLabel="Sending…"
	{pending}
	confirmDisabled={needsAck && !liveAcknowledged}
	confirmDisabledReason="Tick the acknowledgement to continue."
	{error}
	testId="portfolio-action-dialog"
	{oncancel}
	onconfirm={() => onconfirm({ liveAcknowledged: needsAck && liveAcknowledged })}
>
	<div class="row">
		<span>Portfolio</span><span
			>{modeLabel(portfolio.mode)} · {quoteText(
				portfolio.capital_quote,
				portfolio.quote_currency
			)}</span
		>
	</div>
	{#if action === 'start'}
		<p>{START_NOTE}</p>
		<ul class="lines" data-testid="start-lines">
			{#each sleeves as sleeve (sleeve.sleeve_id)}
				<li>
					<span>{sleeve.strategy_name}</span>
					<span class="faint">{weightPercent(sleeve.weight_fraction)}</span>
					<span
						>{quoteText(sleeve.capital_quote, portfolio.quote_currency)}
						{live ? 'allocated' : 'paper cash'}</span
					>
				</li>
			{/each}
		</ul>
		<p class="faint small">
			A sleeve that already runs here is kept as it is. If any sleeve is refused (its strategy runs
			elsewhere, or the risk policy says no), nothing starts and each reason is listed.
		</p>
	{:else if action === 'pause'}
		<p>No new entries. Open positions keep their protective exits, and exits still run.</p>
	{:else if action === 'resume'}
		<p>
			New entries resume on the next closed bar. Every entry still passes the portfolio limits and
			the account risk policy.
		</p>
	{:else if action === 'stop'}
		<fieldset class="stop-mode">
			<legend>Shutdown mode</legend>
			<label class="choice">
				<input type="radio" name="portfolio-stop-mode" bind:group={stopWithFlatten} value={false} />
				<span>
					<strong>Managed stop — keep protection</strong>
					Stop new entries and cancel risk-increasing entry orders. Protective exits stay until positions
					close.
				</span>
			</label>
			<label class="choice">
				<input type="radio" name="portfolio-stop-mode" bind:group={stopWithFlatten} value={true} />
				<span>
					<strong>Stop and flatten</strong>
					Exit every position at market, then cancel remaining orders. Exit price and fees are not guaranteed.
				</span>
			</label>
		</fieldset>
	{:else if action === 'reset'}
		<p>
			Clears the latched {breakerLabel(deployment?.breaker.reason_code ?? null).toLowerCase()} and re-baselines
			the day's open and the peak at today's equity. Sleeves stay paused until you resume them.
		</p>
		{#if deployment?.breaker.detail}<p class="faint small">{deployment.breaker.detail}</p>{/if}
	{/if}
	{#if needsAck}
		<label class="live-ack">
			<input type="checkbox" bind:checked={liveAcknowledged} data-testid="portfolio-live-ack" />
			<span>I understand this places real orders on Coinbase with real money.</span>
		</label>
	{/if}
</ConfirmDialog>

<style>
	.row {
		display: flex;
		justify-content: space-between;
		gap: 12px;
		color: var(--muted);
		font-size: var(--fs-sm);
	}
	.lines {
		display: grid;
		gap: 6px;
		margin: 0;
		padding: 0;
		list-style: none;
	}
	.lines li {
		display: grid;
		grid-template-columns: minmax(0, 1fr) auto auto;
		gap: 12px;
		padding: 6px 0;
		border-top: 1px solid var(--line);
	}
	.lines li span:first-child {
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.stop-mode {
		display: grid;
		gap: 10px;
		margin: 0;
		padding: 10px 12px;
		border: 1px solid var(--line);
		border-radius: var(--radius-md);
	}
	.stop-mode legend {
		padding: 0 4px;
		color: var(--muted);
		font-size: var(--fs-xs);
		text-transform: uppercase;
		letter-spacing: 0.06em;
	}
	.choice {
		display: grid;
		grid-template-columns: auto 1fr;
		gap: 10px;
		color: var(--muted);
		font-size: var(--fs-sm);
		cursor: pointer;
	}
	.choice strong {
		display: block;
		margin-bottom: 2px;
		color: var(--text);
		font-size: var(--fs-base);
	}
	.live-ack {
		display: flex;
		align-items: flex-start;
		gap: 10px;
		padding: 12px;
		border-radius: var(--radius-md);
		background: var(--live-soft);
		color: var(--text);
		cursor: pointer;
	}
	.live-ack input {
		margin-top: 2px;
	}
	.faint {
		color: var(--faint);
	}
	.small {
		font-size: var(--fs-sm);
	}
</style>
