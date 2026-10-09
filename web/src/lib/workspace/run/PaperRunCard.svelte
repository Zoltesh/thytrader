<script lang="ts">
	/**
	 * Run stage Paper card (ADR 0082): this strategy's paper deployments with
	 * their lifecycle controls, and starting paper with the current rules (or
	 * another paper deployment while one is running or paused).
	 */
	import type { LifecycleAction } from '$lib/deployment-detail';
	import type { Deployment } from '$lib/deployments';
	import type { BuilderModel } from '$lib/strategies';
	import DeploymentRuntimeRow from '$lib/workspace/DeploymentRuntimeRow.svelte';
	import PaperStartForm from '$lib/workspace/PaperStartForm.svelte';

	let {
		paper,
		skeleton,
		controlsBlocked,
		strategyId,
		strategyName,
		currentFingerprint,
		fingerprint,
		model,
		canStart,
		onaction,
		onupdated,
		onstarted
	}: {
		paper: Deployment[];
		/** The first deployments load is still running. */
		skeleton: boolean;
		controlsBlocked: boolean;
		strategyId: string;
		strategyName: string | null;
		currentFingerprint: string | null;
		fingerprint: string;
		model: BuilderModel | null;
		canStart: boolean;
		onaction: (action: LifecycleAction, deployment: Deployment) => void;
		onupdated: (result: { stopped: Deployment; started: Deployment | null }) => void;
		onstarted: (deployment: Deployment) => void;
	} = $props();

	const activePaper = $derived(paper.filter((deployment) => deployment.status !== 'stopped'));
</script>

<section class="card run-card" aria-labelledby="paper-title" data-testid="paper-card">
	<div class="card-head">
		<span class="chip paper">Paper</span>
		<h2 id="paper-title">
			{activePaper.length > 0
				? `${activePaper.length} running or paused`
				: paper.length > 0
					? 'Not running'
					: 'No paper deployment'}
		</h2>
	</div>
	<div class="card-body">
		{#if skeleton}
			<div class="skeleton"></div>
		{:else}
			{#each paper as deployment (deployment.id)}
				<DeploymentRuntimeRow
					{deployment}
					disabled={controlsBlocked}
					{currentFingerprint}
					current={model}
					{onaction}
					{onupdated}
				/>
			{:else}
				<p class="muted">No paper deployment of this strategy.</p>
			{/each}
		{/if}
		{#if model && canStart}
			{#if activePaper.length > 0}
				<details class="start-another">
					<summary>Start another paper deployment</summary>
					<PaperStartForm
						{strategyId}
						currentFingerprint={fingerprint}
						name={strategyName ?? model.name}
						{model}
						disabled={controlsBlocked}
						onStarted={onstarted}
					/>
				</details>
			{:else}
				<h3 class="sub">Start paper with the current rules</h3>
				<PaperStartForm
					{strategyId}
					currentFingerprint={fingerprint}
					name={strategyName ?? model.name}
					{model}
					disabled={controlsBlocked}
					onStarted={onstarted}
				/>
			{/if}
		{/if}
	</div>
</section>

<style>
	.card-head {
		display: flex;
		align-items: center;
		gap: 10px;
		padding: 14px 16px;
		border-bottom: 1px solid var(--line);
	}
	.card-body {
		display: grid;
		gap: 10px;
		padding: 16px;
	}
	.sub {
		margin: 8px 0 0;
		color: var(--faint);
		font-size: var(--fs-sm);
		font-weight: 500;
		letter-spacing: 0.05em;
		text-transform: uppercase;
	}
	.muted {
		margin: 0;
		color: var(--muted);
	}
	.start-another summary {
		color: var(--muted);
		cursor: pointer;
	}
	.start-another[open] summary {
		margin-bottom: 10px;
	}
</style>
