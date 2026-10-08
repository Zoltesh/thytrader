<script lang="ts">
	/** Evidence fingerprints of a backtest result: result, strategy, dataset, and run. */
	import { shortFingerprint, type BacktestResult } from '$lib/backtests';

	let { resultFingerprint, result }: { resultFingerprint: string; result: BacktestResult } =
		$props();
</script>

<details class="evidence" data-testid="result-evidence">
	<summary>Evidence <span class="faint">fingerprints</span></summary>
	<div class="provenance">
		<span title={resultFingerprint}>Result <code>{shortFingerprint(resultFingerprint)}</code></span
		><span title={result.strategy_fingerprint}
			>Strategy <code>{shortFingerprint(result.strategy_fingerprint)}</code></span
		><span title={result.dataset_fingerprint}
			>Dataset <code>{shortFingerprint(result.dataset_fingerprint)}</code></span
		><span title={result.run_fingerprint}
			>Run <code>{shortFingerprint(result.run_fingerprint)}</code></span
		>
	</div>
</details>

<style>
	.faint {
		color: var(--faint);
	}
	.evidence {
		border: 1px solid var(--line);
		border-radius: 13px;
		background: var(--surface);
	}
	.evidence summary {
		padding: 12px 18px;
		cursor: pointer;
		font-weight: 600;
	}
	.evidence summary .faint {
		margin-left: 6px;
		font-weight: 400;
		font-size: var(--fs-sm);
	}
	.evidence .provenance {
		border: 0;
		border-top: 1px solid var(--line);
		border-radius: 0;
	}
	.provenance {
		border: 1px solid var(--line);
		border-radius: 13px;
		background: var(--surface);
		display: flex;
		flex-wrap: wrap;
		gap: 12px;
		padding: 14px 18px;
		color: var(--faint);
		font-size: 11px;
	}
	code {
		color: var(--code);
	}
</style>
