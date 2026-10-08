<script lang="ts">
	/** Why a portfolio start was rejected: each sleeve's (or the portfolio's) problem and code. */
	import type { BacktestProblem } from '$lib/portfolios';

	let { problems }: { problems: BacktestProblem[] } = $props();
</script>

<section class="card problems" role="alert" data-testid="portfolio-start-problems">
	<strong>The portfolio was not started. Nothing changed.</strong>
	<ul>
		{#each problems as problem, index (index)}
			<li>
				<b>{problem.strategy_name ?? 'Portfolio'}</b>: {problem.message}
				<span class="faint small">({problem.code})</span>
			</li>
		{/each}
	</ul>
</section>

<style>
	.problems {
		display: grid;
		gap: 8px;
		margin-top: 16px;
		padding: 14px 18px;
		border-color: var(--neg);
	}
	.problems ul {
		margin: 0;
		padding-left: 18px;
	}
	.faint {
		color: var(--faint);
	}
	.small {
		font-size: var(--fs-sm);
	}
</style>
