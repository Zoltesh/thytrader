<script lang="ts">
	/**
	 * Open books of a bot with their stop, target, last-bar unrealized PnL,
	 * time held (ADR 0098), and truthful protection badge (ADR 0112).
	 */
	import { displayQuoteOf } from '$lib/deployment-detail';
	import type { DeploymentPosition } from '$lib/deployments';
	import { heldText, markTitle, unrealizedText } from '$lib/open-books';
	import { protectionBadge } from '$lib/protection-evidence';

	let {
		positions,
		now
	}: {
		positions: DeploymentPosition[];
		/** Clock for the held time. */
		now: number;
	} = $props();
</script>

<section class="card" aria-labelledby="positions-title">
	<div class="card-head"><h2 id="positions-title">Positions &amp; protection</h2></div>
	<!-- svelte-ignore a11y_no_noninteractive_tabindex -->
	<div class="table-scroll" tabindex="0" role="region" aria-label="Open positions">
		<table>
			<caption class="sr-only">Open positions with protection status</caption>
			<thead>
				<tr>
					<th scope="col">Product</th>
					<th scope="col">Side</th>
					<th scope="col" class="num">Quantity</th>
					<th scope="col" class="num">Entry</th>
					<th scope="col" class="num">Stop</th>
					<th scope="col" class="num">Target</th>
					<th scope="col" class="num">Unrealized</th>
					<th scope="col" class="num">Held</th>
					<th scope="col">Protection</th>
				</tr>
			</thead>
			<tbody>
				{#each positions as position (position.product_id)}
					{@const pnl = unrealizedText(position, displayQuoteOf(position.product_id) ?? '')}
					{@const badge = protectionBadge(position, { fallback: 'sentence' })}
					<tr>
						<td>{position.product_id}</td>
						<td>{position.side ?? 'long'}</td>
						<td class="num">{position.quantity}</td>
						<td class="num">{position.entry_price}</td>
						<td class="num">{position.stop_price}</td>
						<td class="num">{position.target_price ?? 'none'}</td>
						<td
							class="num upnl {pnl?.tone ?? 'muted'}"
							title={markTitle(position)}
							data-testid="position-upnl">{pnl?.text ?? '—'}</td
						>
						<td
							class="num muted"
							title="Entry bar {position.entered_bar.slice(0, 16)} UTC"
							data-testid="position-held">{heldText(position.entered_bar, now)}</td
						>
						<td data-testid="position-state" class="state-cell {badge.tone}" title={badge.title}
							>{badge.text}{#if position.protection}<span
									class="protection-evidence"
									data-testid="protection-evidence">{badge.detail}</span
								>{/if}</td
						>
					</tr>
				{/each}
			</tbody>
		</table>
	</div>
</section>

<style>
	.upnl {
		font-variant-numeric: tabular-nums;
	}
	.upnl.pos {
		color: var(--pos);
	}
	.upnl.neg {
		color: var(--neg);
	}
	.state-cell::before {
		display: inline-block;
		width: 6px;
		height: 6px;
		margin-right: 6px;
		border-radius: 50%;
		background: var(--faint);
		vertical-align: 1px;
		content: '';
	}
	.state-cell.ok::before {
		background: var(--accent);
	}
	.state-cell.warn::before {
		background: var(--warn);
	}
	.state-cell.bad::before {
		background: var(--neg);
	}
	.protection-evidence {
		display: block;
		color: var(--muted);
		font-size: var(--fs-xs);
	}
	.card {
		margin-bottom: 16px;
	}
	.card-head {
		display: flex;
		align-items: center;
		gap: 10px;
		padding: 12px 16px;
		border-bottom: 1px solid var(--line);
	}
	.card-head h2 {
		margin-right: auto;
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
	.table-scroll {
		overflow-x: auto;
	}
	table {
		width: 100%;
		border-collapse: collapse;
	}
	th,
	td {
		padding: 9px 16px;
		text-align: left;
		font-size: var(--fs-sm);
	}
	th.num,
	td.num {
		text-align: right;
	}
	td {
		border-top: 1px solid var(--line);
	}
</style>
