<script lang="ts">
	/**
	 * "Run a study" settings: the study kind, OOS fraction or walk-forward fold
	 * geometry, and (for sweeps and WFO) one parameter axis with its selection
	 * metric. Changing the axis target keeps the parameter valid for it.
	 */
	import {
		axisNeedsIndicator,
		parametersForTarget,
		type SweepAxisTarget
	} from '$lib/research-studies';
	import type { StudyDraft } from './launch-plan';

	let {
		study = $bindable(),
		launching,
		launchBlocked,
		onrun
	}: {
		study: StudyDraft;
		launching: boolean;
		launchBlocked: boolean;
		onrun: () => void;
	} = $props();

	function onAxisTargetChange(event: Event): void {
		const value = (event.currentTarget as HTMLSelectElement).value as SweepAxisTarget;
		study.axisTarget = value;
		const allowed = parametersForTarget(value);
		if (!allowed.includes(study.axisParameter)) {
			study.axisParameter = allowed[0] ?? 'period';
		}
		if (!axisNeedsIndicator(value)) {
			study.axisConditionOperator = '';
		}
	}
</script>

<div class="study-body" id="run-study-panel">
	<div class="launch-grid">
		<label
			>Study
			<select bind:value={study.kind}>
				<option value="oos_holdout">OOS holdout</option>
				<option value="walk_forward">Walk-forward</option>
				<option value="parameter_sweep">Parameter sweep</option>
				<option value="walk_forward_optimization">Walk-forward optimization</option>
			</select></label
		>
	</div>
	{#if study.kind === 'oos_holdout'}
		<div class="launch-grid">
			<label
				>OOS fraction (last share) <input
					inputmode="decimal"
					bind:value={study.oosFraction}
				/></label
			>
		</div>
		<p class="view-note">
			The same rules snapshot is simulated on in-sample then out-of-sample. OOS is the honest claim;
			this does not retune parameters.
		</p>
	{/if}
	{#if study.kind === 'walk_forward' || study.kind === 'walk_forward_optimization'}
		<div class="launch-grid">
			<label>In-sample bars<input inputmode="numeric" bind:value={study.inSampleBars} /></label>
			<label>OOS bars<input inputmode="numeric" bind:value={study.outOfSampleBars} /></label>
			<label>Step bars<input inputmode="numeric" bind:value={study.stepBars} /></label>
			<label
				>Fold mode
				<select bind:value={study.foldMode}>
					<option value="rolling">Rolling</option>
					<option value="anchored">Anchored</option>
				</select></label
			>
		</div>
		<p class="view-note">
			{#if study.kind === 'walk_forward'}
				Walk-forward validation uses the same rules snapshot on each fold. Non-overlapping OOS
				windows may include a derived stitched equity curve. Cross-market studies stay on the
				research CLI.
			{:else}
				WFO simulates every candidate on every fold and selects only on in-sample
				{study.selectionMetric}. The matching OOS window is the claim. Stitched equity compounds
				selected OOS returns without interpolating embargo gaps.
			{/if}
		</p>
	{/if}
	{#if study.kind === 'parameter_sweep' || study.kind === 'walk_forward_optimization'}
		<div class="launch-grid">
			<label
				>Axis target
				<select value={study.axisTarget} onchange={onAxisTargetChange}>
					<option value="indicator">indicator</option>
					<option value="sizing">sizing</option>
					<option value="exits">exits</option>
					<option value="execution">execution</option>
					<option value="entry_literal">entry_literal</option>
					<option value="htf_literal">htf_literal</option>
				</select></label
			>
			{#if axisNeedsIndicator(study.axisTarget)}
				<label>Indicator id<input bind:value={study.axisIndicatorId} /></label>
			{/if}
			<label
				>Parameter
				<select bind:value={study.axisParameter}>
					{#each parametersForTarget(study.axisTarget) as parameter (parameter)}
						<option value={parameter}>{parameter}</option>
					{/each}
				</select></label
			>
			<label>Axis values (comma-separated) <input bind:value={study.axisValues} /></label>
			{#if study.axisTarget === 'entry_literal' || study.axisTarget === 'htf_literal'}
				<label
					>Condition operator (optional)
					<input bind:value={study.axisConditionOperator} /></label
				>
			{/if}
			<label
				>Selection metric
				<select bind:value={study.selectionMetric}>
					<option value="total_return_fraction">Total return</option>
					<option value="total_net_pnl">Total net PnL</option>
					<option value="maximum_drawdown_fraction">Max drawdown</option>
				</select></label
			>
		</div>
		{#if study.kind === 'parameter_sweep'}
			<p class="view-note">
				Each axis cell is one derived candidate on the same window. The aggregate is not an
				out-of-sample claim. Submit records a snapshot of each derived candidate. Product and
				timeframe are not sweepable.
			</p>
		{/if}
	{/if}
	<button class="btn" type="button" onclick={() => onrun()} disabled={launchBlocked}
		>{launching ? 'Running simulation…' : 'Run study'}</button
	>
</div>

<style>
	.study-body {
		display: grid;
		gap: 10px;
		padding: 12px;
		border: 1px solid var(--line);
		border-radius: var(--radius-md);
		background: var(--surface-2);
	}
	.study-body .btn {
		justify-self: start;
	}
	.view-note {
		margin: 0;
		color: var(--muted);
		font-size: var(--fs-sm);
	}
	.launch-grid {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
		gap: 10px;
	}
	.launch-grid label {
		display: grid;
		gap: 4px;
		color: var(--muted);
		font-size: var(--fs-sm);
	}
	.launch-grid input,
	.launch-grid select {
		width: 100%;
		min-height: 34px;
		padding: 6px 9px;
		border: 1px solid var(--line-2);
		border-radius: var(--radius-md);
		background: var(--surface);
		color: var(--text);
		font: inherit;
		font-size: var(--fs-sm);
	}
</style>
