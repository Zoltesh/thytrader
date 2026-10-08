<script lang="ts">
	/**
	 * What each sleeve contributed to a portfolio backtest: weight, contribution in
	 * points, its return and drawdown alone, trades, correlation to the rest, and
	 * the cash row (reserve and unallocated).
	 */
	import { subtractDecimalStrings } from '$lib/portfolio';
	import {
		baseAsset,
		coefficientText,
		contributionPoints,
		drawdownPercent,
		signedPercent,
		weightPercent,
		type PortfolioBacktestResult
	} from '$lib/portfolios';
	import { tone } from './backtest';

	let { result }: { result: PortfolioBacktestResult } = $props();
</script>

<section class="card" aria-label="What each sleeve contributed">
	<div class="card-head"><h2>What each sleeve contributed</h2></div>
	<div class="table-wrap">
		<table data-testid="contribution-table">
			<thead>
				<tr>
					<th scope="col">Sleeve</th>
					<th scope="col">Weight</th>
					<th scope="col">Contribution</th>
					<th scope="col">Alone</th>
					<th scope="col">Trades</th>
					<th scope="col">Max DD alone</th>
					<th scope="col">Correlation to rest</th>
				</tr>
			</thead>
			<tbody>
				{#each result.sleeves as sleeve (sleeve.sleeve_id)}
					<tr>
						<td
							>{sleeve.strategy_name}
							<span class="faint">{baseAsset(sleeve.product_id)}</span></td
						>
						<td>{weightPercent(sleeve.weight_fraction)}</td>
						<td class={tone(sleeve.contribution_fraction)}
							>{contributionPoints(sleeve.contribution_fraction)}</td
						>
						<td class={tone(sleeve.total_return_fraction)}
							>{signedPercent(sleeve.total_return_fraction)}</td
						>
						<td>{sleeve.trade_count}</td>
						<td>{drawdownPercent(sleeve.maximum_drawdown_fraction)}</td>
						<td>{coefficientText(sleeve.correlation_to_rest)}</td>
					</tr>
				{/each}
				<tr>
					<td>Cash (reserve and unallocated)</td>
					<td>{weightPercent(subtractDecimalStrings('1', result.summary.allocated_fraction))}</td>
					<td>0.00 pts</td>
					<td>—</td>
					<td>—</td>
					<td>—</td>
					<td>—</td>
				</tr>
			</tbody>
		</table>
	</div>
</section>

<style>
	.card {
		margin-top: 16px;
	}
	.card-head {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 10px;
		padding: 14px 16px;
		border-bottom: 1px solid var(--line);
	}
	.pos {
		color: var(--pos);
	}
	.neg {
		color: var(--neg);
	}
	.faint {
		color: var(--faint);
	}
</style>
