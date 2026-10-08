<script lang="ts">
	/**
	 * The portfolio backtest run card: maker/taker fee, slippage and spread-stress
	 * fields with the fee source chip, the Run button, the job's progress, and a
	 * rejected run's per-sleeve problems. `BacktestTab.svelte` owns the run.
	 */
	import {
		isJobActive,
		jobProgressText,
		windowText,
		type BacktestProblem,
		type PortfolioBacktestJob
	} from '$lib/portfolios';

	let {
		maker = $bindable(),
		taker = $bindable(),
		slippage = $bindable(),
		spread = $bindable(),
		feeChip,
		formProblem,
		submitting,
		running,
		job,
		runError,
		problems,
		onfeeedit,
		onrun
	}: {
		maker: string;
		taker: string;
		slippage: string;
		spread: string;
		/** Where the fee rates came from (suggested tier, edited, unavailable). */
		feeChip: string;
		/** Why the form cannot run, or null. */
		formProblem: string | null;
		submitting: boolean;
		running: boolean;
		job: PortfolioBacktestJob | null;
		runError: string | null;
		problems: BacktestProblem[];
		/** The operator edited a fee rate, so a suggestion never overwrites it. */
		onfeeedit: () => void;
		/** Submit the run. */
		onrun: () => void;
	} = $props();
</script>

<section class="card run" aria-label="Run a portfolio backtest">
	<div class="run-head">
		<h2>Run a portfolio backtest</h2>
		<span class="faint small"
			>Every sleeve runs the unified backtest model on its own slice of capital over the sleeves'
			common window, using each one's latest complete datasets.</span
		>
	</div>
	<div class="fields">
		<label class="field">
			<span>Maker fee rate</span>
			<input
				type="text"
				inputmode="decimal"
				bind:value={maker}
				oninput={onfeeedit}
				placeholder="0.004"
			/>
		</label>
		<label class="field">
			<span>Taker fee rate</span>
			<input
				type="text"
				inputmode="decimal"
				bind:value={taker}
				oninput={onfeeedit}
				placeholder="0.006"
			/>
		</label>
		<label class="field">
			<span>Slippage (bps)</span>
			<input type="text" inputmode="decimal" bind:value={slippage} />
		</label>
		<label class="field">
			<span>Spread stress (bps, optional)</span>
			<input type="text" inputmode="decimal" bind:value={spread} placeholder="0" />
		</label>
		<button
			type="button"
			class="btn primary run-button"
			disabled={formProblem !== null || submitting || running}
			onclick={onrun}
			>{submitting ? 'Starting…' : running ? 'Running…' : 'Run portfolio backtest'}</button
		>
	</div>
	<div class="run-foot">
		<span class="chip" data-testid="fee-source">{feeChip}</span>
		{#if formProblem !== null}<span class="faint small">{formProblem}</span>{/if}
	</div>
	{#if job !== null}
		<div
			class="progress"
			class:failed={job.status === 'failed' || job.status === 'expired'}
			role="status"
			data-testid="backtest-progress"
		>
			{#if isJobActive(job)}<span class="pulse" aria-hidden="true"></span>{/if}
			<span>{jobProgressText(job)}</span>
			{#if isJobActive(job)}
				<span class="faint small"
					>{job.progress_current} of {job.progress_total} steps · {windowText(
						job.evaluation_start,
						job.evaluation_end
					)}</span
				>
			{/if}
		</div>
	{/if}
	{#if runError}
		<div class="problem-box" role="alert" data-testid="backtest-error">
			<p>{runError}</p>
			{#if problems.length > 0}
				<ul>
					{#each problems as problem, index (index)}
						<li>
							{#if problem.strategy_name}<strong>{problem.strategy_name}:</strong>{/if}
							{problem.message}
						</li>
					{/each}
				</ul>
			{/if}
		</div>
	{/if}
</section>

<style>
	.card {
		margin-top: 16px;
	}
	.run {
		padding: 16px 18px;
	}
	.run-head {
		display: grid;
		gap: 4px;
		margin-bottom: 12px;
	}
	.fields {
		display: grid;
		grid-template-columns: repeat(4, minmax(0, 1fr)) auto;
		align-items: end;
		gap: 10px;
	}
	.field {
		display: grid;
		gap: 6px;
		color: var(--muted);
		font-size: var(--fs-sm);
	}
	.field input {
		min-height: 34px;
		padding: 0 10px;
		border: 1px solid var(--line-2);
		border-radius: var(--radius-md);
		background: var(--surface-2);
		color: var(--text);
	}
	.run-foot {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 10px;
		margin-top: 10px;
	}
	.progress {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 10px;
		margin-top: 12px;
		padding: 10px 12px;
		border: 1px solid var(--accent-line);
		border-radius: var(--radius-md);
		background: var(--accent-soft);
	}
	.progress.failed {
		border-color: var(--danger-line);
		background: var(--danger-soft);
		color: var(--neg);
	}
	.pulse {
		width: 8px;
		height: 8px;
		border-radius: 50%;
		background: var(--accent);
	}
	@media (prefers-reduced-motion: no-preference) {
		.pulse {
			animation: pulse 1.2s ease-in-out infinite;
		}
	}
	@keyframes pulse {
		50% {
			opacity: 0.3;
		}
	}
	.problem-box {
		margin-top: 12px;
		padding: 10px 12px;
		border: 1px solid var(--danger-line);
		border-radius: var(--radius-md);
		background: var(--danger-soft);
		color: var(--neg);
	}
	.problem-box p {
		margin: 0;
	}
	.problem-box ul {
		margin: 8px 0 0;
		padding-left: 18px;
		color: var(--text);
	}
	.faint {
		color: var(--faint);
	}
	.small {
		font-size: var(--fs-sm);
	}
	@media (max-width: 1100px) {
		.fields {
			grid-template-columns: repeat(2, minmax(0, 1fr));
		}
	}
</style>
