<script lang="ts">
	/**
	 * The sleeves table: one row per sleeve with its strategy and note, weight
	 * (an input while weights are edited), market, capital, its portfolio bot
	 * with open books, twin link, and PnL, bots of the same strategy outside the
	 * portfolio, issues, and per-sleeve Start / Pause / Resume / Stop or Remove.
	 */
	import { resolve } from '$app/paths';
	import { marketLabel } from '$lib/deployment-detail';
	import type { Deployment } from '$lib/deployments';
	import type { PaperLiveFillComparison } from '$lib/fill-comparison';
	import OpenBookSummary from '$lib/OpenBookSummary.svelte';
	import {
		botStatusText,
		pausedByBreaker,
		quoteText,
		signedQuote,
		sleeveBots,
		sleeveIssueText,
		sleevePositionText,
		weightPercent,
		type AllocationBar,
		type Portfolio,
		type PortfolioDeployment,
		type PortfolioDialogAction,
		type PortfolioSleeve
	} from '$lib/portfolios';
	import { botLabel, occupied, sleeveBot, twinOf } from './sleeves';

	let {
		portfolio,
		deployment,
		inventory,
		editing,
		draft = $bindable(),
		draftFractions,
		bars,
		now,
		fills,
		onaction,
		onremove
	}: {
		portfolio: Portfolio;
		/** The portfolio's deployment view (sleeve bots), or null before it loads. */
		deployment: PortfolioDeployment | null;
		inventory: Deployment[] | null;
		/** Weights are being edited: each row shows a weight input bound to `draft`. */
		editing: boolean;
		/** Draft weight percents by sleeve id. */
		draft: Record<string, string>;
		/** Parsed draft weights in sleeve order; null marks an invalid input. */
		draftFractions: (string | null)[];
		bars: AllocationBar[];
		/** Clock for the open books' held time. */
		now: number;
		/** Paper vs live twins of this portfolio's sleeves (ADR 0098). */
		fills: PaperLiveFillComparison[];
		/** Open the confirmation for one sleeve's start, pause, resume, or stop. */
		onaction: (action: PortfolioDialogAction, sleeveId: string) => void;
		/** Ask to remove a sleeve whose bot does not hold it. */
		onremove: (sleeve: PortfolioSleeve) => void;
	} = $props();
</script>

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
				{@const bot = sleeveBot(deployment, sleeve.sleeve_id)}
				{@const others = bots.filter((item) => item.id !== bot?.deployment_id)}
				<tr data-testid="sleeve-row">
					<td>
						<a class="name" href={resolve(`/strategies/${encodeURIComponent(sleeve.strategy_id)}`)}
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
								+{sleeve.covered_product_ids.length - 1} more product{sleeve.covered_product_ids
									.length === 2
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
							{@const books = bot.books ?? []}
							{@const twin = twinOf(fills, bot.deployment_id)}
							{#if twin !== null}
								<a class="twin" href="#paper-live-fills" data-testid="sleeve-twin"
									>{twin === 'live' ? 'Live twin' : 'Paper twin'} · fills</a
								>
							{/if}
							{#if books.length > 0}
								<span class="sr-only" data-testid="sleeve-position-state">{positionState}</span>
								{#each books as book (book.product_id)}
									<OpenBookSummary
										{book}
										quote={portfolio.quote_currency}
										{now}
										showProduct={books.length > 1}
									/>
								{/each}
							{:else if positionState !== null}
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
							{#each sleeve.issues as issue, index (index)}
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
								onclick={() => onremove(sleeve)}>Remove…</button
							>
						{/if}
					</td>
				</tr>
			{/each}
		</tbody>
	</table>
</div>

<style>
	.twin {
		display: inline-block;
		margin-top: 2px;
		color: var(--muted);
		font-size: var(--fs-xs);
		text-decoration: none;
	}
	.twin:hover {
		color: var(--text);
		text-decoration: underline;
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
	.faint {
		color: var(--faint);
	}
	.muted {
		color: var(--muted);
	}
	.small {
		font-size: var(--fs-sm);
	}
</style>
