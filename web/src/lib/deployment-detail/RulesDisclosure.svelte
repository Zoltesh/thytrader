<script lang="ts">
	/**
	 * "Exact rules this bot runs" disclosure: the fingerprint, then the config
	 * summary of that rules snapshot. An unavailable snapshot says so and never
	 * substitutes the strategy's current edit.
	 */
	import { fingerprintText } from '$lib/deployment-detail';
	import type { Deployment } from '$lib/deployments';
	import type { StrategySourceState } from '$lib/strategy-config';

	let {
		current,
		strategyConfig
	}: {
		current: Deployment;
		/** Null while loading (or for a discretionary deployment). */
		strategyConfig: StrategySourceState | null;
	} = $props();
</script>

<details class="card disclosure" data-testid="config-disclosure">
	<summary>
		Exact rules this bot runs
		<code data-testid="deployment-fingerprint">{fingerprintText(current)}</code>
	</summary>
	<div class="pad">
		{#if current.strategy_fingerprint === null}
			<p class="quiet">Discretionary deployments have no strategy rules snapshot.</p>
		{:else if strategyConfig === null}
			<p class="quiet" data-testid="strategy-config-loading">Loading configuration…</p>
		{:else if strategyConfig.kind === 'unavailable'}
			<p class="contract-note" data-testid="strategy-config-unavailable" role="status">
				Rules snapshot unavailable ({strategyConfig.reason}). The fingerprint above is the only
				identity shown; the strategy's current edit was not substituted.
			</p>
		{:else}
			<div class="config-summary" data-testid="strategy-config-summary">
				<p class="config-rule">{strategyConfig.summary.rule}</p>
				<dl class="kv">
					{#each strategyConfig.summary.identity as fact (fact.label)}
						<div>
							<dt>{fact.label}</dt>
							<dd>{fact.value}</dd>
						</div>
					{/each}
				</dl>
				<p class="config-section">Indicators</p>
				<ul class="config-list">
					{#each strategyConfig.summary.indicators as line, index (index)}
						<li>{line}</li>
					{/each}
				</ul>
				<p class="config-section">Entry</p>
				<p>{strategyConfig.summary.entry}</p>
				{#if strategyConfig.summary.htfEntry !== null}
					<p class="config-section">HTF filter</p>
					<p>{strategyConfig.summary.htfEntry}</p>
				{/if}
				<p class="config-section">Exits</p>
				<ul class="config-list">
					{#each strategyConfig.summary.exits as line, index (index)}
						<li>{line}</li>
					{/each}
				</ul>
				<p class="config-section">Sizing &amp; limits</p>
				<ul class="config-list">
					{#each strategyConfig.summary.sizing as line, index (index)}
						<li>{line}</li>
					{/each}
				</ul>
			</div>
		{/if}
	</div>
</details>

<style>
	.card {
		margin-bottom: 16px;
	}
	.pad {
		margin: 0;
		padding: 14px 16px;
	}
	.quiet {
		color: var(--muted);
	}
	.contract-note {
		margin: 0 0 12px;
		color: var(--warn);
		font-size: var(--fs-sm);
	}
	.disclosure summary {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 6px 12px;
		padding: 12px 16px;
		cursor: pointer;
		font-weight: 600;
	}
	.disclosure summary code {
		font-weight: 400;
		font-size: var(--fs-xs);
		word-break: break-all;
	}
	.disclosure[open] summary {
		border-bottom: 1px solid var(--line);
	}
	.kv {
		display: flex;
		flex-wrap: wrap;
		gap: 10px 28px;
		margin: 0 0 12px;
	}
	.kv dt {
		color: var(--faint);
		font-size: var(--fs-xs);
	}
	.kv dd {
		margin: 2px 0 0;
		font-family: var(--font-mono);
		font-size: var(--fs-sm);
	}
	.config-summary {
		display: grid;
		gap: 6px;
	}
	.config-summary p {
		margin: 0;
	}
	.config-section {
		margin-top: 4px;
		color: var(--muted);
		font-size: var(--fs-xs);
		text-transform: uppercase;
		letter-spacing: 0.06em;
	}
	.config-list {
		display: grid;
		gap: 2px;
		margin: 0;
		padding-left: 18px;
	}
</style>
