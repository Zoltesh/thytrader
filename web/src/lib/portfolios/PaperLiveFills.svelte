<script lang="ts">
	/**
	 * Paper vs live (ADR 0097, ADR 0098): entry fills of paper and live books
	 * that run the same strategy snapshot as one of this portfolio's sleeves.
	 * Each twin is two rows (paper, live): fill rate as a small bar plus the
	 * count, the average fill against the posted limit in bps (positive is
	 * worse), and the median wait from rest to fill. Mode chips carry the mode
	 * in words; the paper/live colors follow the app's mode convention.
	 */
	import { resolve } from '$app/paths';
	import { marketLabel } from '$lib/deployment-detail';
	import {
		fillCountText,
		fillShare,
		outcomeText,
		portfolioSide,
		slippageText,
		waitGapText,
		waitText,
		type EntryFillDigest,
		type PaperLiveFillComparison
	} from '$lib/fill-comparison';

	let {
		portfolioId,
		rows,
		warnings = []
	}: {
		portfolioId: string;
		rows: PaperLiveFillComparison[];
		warnings?: string[];
	} = $props();

	function sides(
		row: PaperLiveFillComparison
	): { mode: 'paper' | 'live'; digest: EntryFillDigest }[] {
		return [
			{ mode: 'paper', digest: row.paper },
			{ mode: 'live', digest: row.live }
		];
	}

	function shareWidth(digest: EntryFillDigest): string {
		const share = fillShare(digest);
		return share === null ? '0%' : `${Math.round(share * 100)}%`;
	}
</script>

<section
	class="card plf"
	id="paper-live-fills"
	aria-labelledby="plf-title"
	data-testid="paper-live-fills"
>
	<div class="head">
		<h2 id="plf-title">Paper vs live</h2>
		<span class="faint small">Entry fills of linked twins on the same rules snapshot</span>
	</div>
	<div class="table-wrap">
		<table>
			<caption class="sr-only"
				>Paper and live entry fills per strategy snapshot: fill rate, average fill against the
				limit, and median time to fill</caption
			>
			<thead>
				<tr>
					<th scope="col" class="left">Strategy</th>
					<th scope="col" class="left">Book</th>
					<th scope="col" class="left">Filled</th>
					<th scope="col">vs limit</th>
					<th scope="col">Median wait</th>
				</tr>
			</thead>
			{#each rows as row (row.paper.deployment_id + row.live.deployment_id)}
				{@const gap = waitGapText(row)}
				{@const own = portfolioSide(row, portfolioId)}
				<tbody data-testid="plf-row">
					{#each sides(row) as side, index (side.mode)}
						{@const slip = slippageText(side.digest)}
						{@const outcomes = outcomeText(side.digest)}
						<tr>
							{#if index === 0}
								<th scope="rowgroup" rowspan="2" class="left strategy">
									<span class="name">{row.strategy_name ?? 'Unnamed strategy'}</span>
									<span class="faint small">{marketLabel(row.product_id)}</span>
									{#if gap !== null}<span class="gap small" data-testid="plf-gap">{gap}</span>{/if}
								</th>
							{/if}
							<td class="left">
								<a
									class="chip {side.mode}"
									href={resolve(`/deployments/${encodeURIComponent(side.digest.deployment_id)}`)}
									title="Open the {side.mode} bot ({side.digest.status})"
									>{side.mode === 'live' ? 'LIVE' : 'Paper'}</a
								>
								{#if own === side.mode || own === 'both'}
									<span class="faint small">this portfolio</span>
								{/if}
							</td>
							<td class="left">
								<div class="rate">
									<div class="track" aria-hidden="true">
										<div class="fill {side.mode}" style:width={shareWidth(side.digest)}></div>
									</div>
									<span data-testid="plf-filled">{fillCountText(side.digest)}</span>
								</div>
								{#if outcomes}<div class="faint small">{outcomes}</div>{/if}
							</td>
							<td class="num {slip?.tone ?? 'muted'}" data-testid="plf-slippage"
								>{slip?.text ?? '—'}</td
							>
							<td class="num" data-testid="plf-wait"
								>{waitText(side.digest.median_seconds_to_fill)}</td
							>
						</tr>
					{/each}
				</tbody>
			{/each}
		</table>
	</div>
	<p class="foot faint small">
		Paper fills only when a later closed candle trades through the limit, so its wait runs to that
		bar's close; live fills when Coinbase matches the order. Positive bps is worse than the posted
		limit.
	</p>
	{#each warnings as warning, index (index)}<p class="foot warn small" role="status">
			{warning}
		</p>{/each}
</section>

<style>
	.plf {
		overflow: hidden;
	}
	.head {
		display: flex;
		flex-wrap: wrap;
		align-items: baseline;
		gap: 10px;
		padding: 14px 16px 10px;
	}
	table {
		width: 100%;
		border-collapse: collapse;
		font-size: var(--fs-base);
	}
	th,
	td {
		padding: 8px 16px;
		border: 0;
		text-align: right;
		vertical-align: middle;
		white-space: nowrap;
	}
	tbody {
		border-top: 1px solid var(--line);
	}
	thead th {
		border-bottom: 1px solid var(--line);
		color: var(--faint);
		font-size: var(--fs-xs);
		font-weight: 500;
		letter-spacing: 0.04em;
		text-transform: uppercase;
	}
	.left {
		text-align: left;
	}
	.strategy {
		display: table-cell;
		font-weight: 400;
		vertical-align: top;
		white-space: normal;
	}
	.strategy > span {
		display: block;
	}
	.name {
		color: var(--text);
		font-weight: 500;
	}
	.gap {
		margin-top: 4px;
		color: var(--muted);
	}
	a.chip {
		text-decoration: none;
	}
	.rate {
		display: flex;
		align-items: center;
		gap: 8px;
	}
	.track {
		width: 56px;
		height: 4px;
		border-radius: 2px;
		background: var(--line);
	}
	.fill {
		height: 4px;
		border-radius: 2px;
	}
	.fill.paper {
		background: var(--accent);
	}
	.fill.live {
		background: var(--live);
	}
	.num {
		font-variant-numeric: tabular-nums;
	}
	.num.pos {
		color: var(--pos);
	}
	.num.neg {
		color: var(--neg);
	}
	.num.muted {
		color: var(--muted);
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
	.foot {
		margin: 0;
		padding: 10px 16px 14px;
		border-top: 1px solid var(--line);
	}
	.foot + .foot {
		border-top: 0;
		padding-top: 0;
	}
</style>
