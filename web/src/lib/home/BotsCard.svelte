<script lang="ts">
	/**
	 * Home "Your bots" (ADR 0084): running, paused, and needs-attention bots,
	 * live and paper, as rows that open `/deployments/{id}`. Rows reuse the
	 * Portfolio row model, so names, positions, protection, and PnL read the
	 * same on both pages; stopped bots stay on Portfolio.
	 */
	import { resolve } from '$app/paths';
	import {
		GROUP_ORDER,
		groupOf,
		portfolioRow,
		type StrategyIdentity
	} from '$lib/deployment-portfolio';
	import type { Deployment } from '$lib/deployments';
	import FuturesBookBadge from '$lib/FuturesBookBadge.svelte';
	import type { Load } from './load';

	let {
		deployments,
		names,
		onretry
	}: {
		deployments: Load<Deployment[]>;
		names: ReadonlyMap<string, StrategyIdentity>;
		onretry: () => void;
	} = $props();

	/** Rows shown on Home; Portfolio lists the rest. */
	const ROW_LIMIT = 8;
	const GROUP_RANK = new Map(GROUP_ORDER.map((group, index) => [group.key, index]));

	const active = $derived(
		(deployments.data ?? [])
			.filter((deployment) => deployment.status !== 'stopped')
			.sort(
				(left, right) =>
					(GROUP_RANK.get(groupOf(left)) ?? 0) - (GROUP_RANK.get(groupOf(right)) ?? 0) ||
					Number(right.mode === 'live') - Number(left.mode === 'live')
			)
	);
	const stoppedCount = $derived(
		(deployments.data ?? []).filter((deployment) => deployment.status === 'stopped').length
	);
	const shown = $derived(active.slice(0, ROW_LIMIT));
</script>

<section class="card bots" aria-labelledby="bots-title" data-testid="your-bots">
	<div class="card-head">
		<h2 id="bots-title">Your bots</h2>
		<a class="btn ghost portfolio-link" href={resolve('/deployments')}>Portfolio →</a>
	</div>
	{#if deployments.data === null}
		{#if deployments.status === 'error'}
			<div class="state error" role="status">
				<p>Couldn't load your bots: {deployments.error}</p>
				<button type="button" class="btn" onclick={onretry}>Retry</button>
			</div>
		{:else}
			<div class="state" aria-label="Loading your bots">
				<div class="skeleton row-skeleton"></div>
				<div class="skeleton row-skeleton"></div>
			</div>
		{/if}
	{:else if active.length === 0}
		<div class="state empty" data-testid="bots-empty">
			<p>
				No running or paused bots{stoppedCount > 0
					? ` (${stoppedCount} stopped on Portfolio)`
					: ''}. Start one from a strategy's Run stage, or place a one-off order on Trade.
			</p>
			<div class="links">
				<a class="btn" href={resolve('/strategies')}>Open Strategies</a>
				<a class="btn ghost" href={resolve('/trade')}>Trade</a>
			</div>
		</div>
	{:else}
		<div class="bot-row head" aria-hidden="true">
			<div>Bot</div>
			<div>Mode</div>
			<div>Market</div>
			<div>Position</div>
			<div class="num">PnL</div>
			<div class="right">Status</div>
		</div>
		<ul role="list">
			{#each shown as deployment (deployment.id)}
				{@const item = portfolioRow(deployment, names)}
				<li data-testid="home-bot-row" data-mode={item.mode}>
					<a
						class="bot-row"
						href={resolve(`/deployments/${encodeURIComponent(deployment.id)}`)}
						aria-label="Open {item.name}, {item.mode === 'live'
							? 'live'
							: 'paper'}, {item.market} {item.clock}, {item.status.toLowerCase()}"
					>
						<div class="who">
							<div class="name">
								{item.name}
								{#if item.rules}<span class="faint">{item.rules}</span>{/if}
							</div>
							{#if item.note}
								<div
									class="note"
									class:problem={deployment.mismatch_detail !== null ||
										deployment.daily_loss_latched ||
										deployment.drawdown_latched}
								>
									{item.note}
								</div>
							{/if}
						</div>
						<div>
							<span
								class="chip"
								class:paper={item.mode === 'paper'}
								class:live={item.mode === 'live'}>{item.modeLabel}</span
							>
							<FuturesBookBadge productId={deployment.product_id} />
						</div>
						<div class="market">{item.market} <span class="faint">· {item.clock}</span></div>
						<div class="position">
							<div>{item.position}</div>
							<div class="faint small">{item.protection}</div>
						</div>
						<div
							class="num"
							class:pos={item.pnlTone === 'pos'}
							class:neg={item.pnlTone === 'neg'}
							class:muted={item.pnlTone === 'muted'}
						>
							{item.pnl}
						</div>
						<div class="right muted">{item.status}</div>
					</a>
				</li>
			{/each}
		</ul>
		{#if active.length > shown.length || deployments.status === 'error'}
			<div class="foot">
				{#if active.length > shown.length}
					<span
						>Showing {shown.length} of {active.length} running or paused bots ·
						<a href={resolve('/deployments')}>see all on Portfolio</a></span
					>
				{/if}
				{#if deployments.status === 'error'}
					<span class="warn" role="status">Couldn't refresh bots: {deployments.error}</span>
					<button type="button" class="btn ghost retry" onclick={onretry}>Retry</button>
				{/if}
			</div>
		{/if}
	{/if}
</section>

<style>
	.card-head {
		display: flex;
		align-items: center;
		gap: 10px;
		padding: 10px 16px;
		border-bottom: 1px solid var(--line);
	}
	.portfolio-link {
		margin-left: auto;
	}
	ul {
		margin: 0;
		padding: 0;
		list-style: none;
	}
	.bot-row {
		display: grid;
		grid-template-columns:
			minmax(0, 2.2fr) 80px minmax(0, 1.2fr) minmax(0, 1.5fr) minmax(0, 0.9fr)
			minmax(0, 0.8fr);
		align-items: center;
		gap: 12px;
		padding: 11px 16px;
		border-bottom: 1px solid var(--line);
		color: var(--text);
		text-decoration: none;
	}
	li:last-child .bot-row {
		border-bottom: 0;
	}
	a.bot-row:hover {
		background: var(--hover);
	}
	a.bot-row:focus-visible {
		outline-offset: -2px;
	}
	.bot-row.head {
		padding-top: 8px;
		padding-bottom: 8px;
		color: var(--faint);
		font-size: 11.5px;
	}
	.name {
		font-weight: 500;
	}
	.note {
		margin-top: 2px;
		color: var(--faint);
		font-size: var(--fs-sm);
	}
	.note.problem {
		color: var(--neg);
	}
	.small {
		font-size: var(--fs-sm);
	}
	.faint {
		color: var(--faint);
	}
	.muted {
		color: var(--muted);
	}
	.pos {
		color: var(--pos);
	}
	.neg {
		color: var(--neg);
	}
	.num {
		text-align: right;
		font-variant-numeric: tabular-nums;
	}
	.right {
		text-align: right;
	}
	.state {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 10px 14px;
		padding: 14px 16px;
		color: var(--muted);
	}
	.state p {
		margin: 0;
	}
	.state.error {
		color: var(--warn);
	}
	.state:not(.error):not(.empty) {
		display: block;
	}
	.links {
		display: flex;
		gap: 8px;
	}
	.row-skeleton {
		height: 34px;
		margin: 6px 0;
	}
	.foot {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 8px 14px;
		padding: 10px 16px;
		border-top: 1px solid var(--line);
		color: var(--faint);
		font-size: var(--fs-sm);
	}
	.warn {
		color: var(--warn);
	}
	.retry {
		min-height: 26px;
		padding: 0 8px;
	}
	@media (prefers-reduced-motion: no-preference) {
		a.bot-row {
			transition: background 0.12s;
		}
	}
	@media (max-width: 900px) {
		.bot-row {
			grid-template-columns: minmax(0, 1fr) auto;
		}
		.bot-row.head {
			display: none;
		}
		.bot-row > .market,
		.bot-row > .position {
			grid-column: 1 / -1;
		}
	}
</style>
