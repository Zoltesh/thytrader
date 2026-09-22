<script lang="ts">
	import { goto } from '$app/navigation';
	import { resolve } from '$app/paths';
	import { onMount } from 'svelte';
	import {
		listDeployments,
		pauseDeployment,
		resumeDeployment,
		stopDeployment,
		canonicalPositions,
		type Deployment
	} from '$lib/deployments';
	import { lifecycleContractNote, lifecycleControlsAvailable } from '$lib/lifecycle-contract';

	let deployments = $state<Deployment[]>([]);
	let loading = $state(true);
	let error = $state<string | null>(null);
	let busyId = $state<string | null>(null);
	let actionError = $state<string | null>(null);

	const running = $derived(deployments.filter((deployment) => deployment.status === 'running'));
	const paused = $derived(deployments.filter((deployment) => deployment.status === 'paused'));
	const attention = $derived(
		deployments.filter(
			(deployment) =>
				deployment.status !== 'running' &&
				deployment.status !== 'paused' &&
				deployment.status !== 'stopped'
		)
	);
	const stoppedItems = $derived(
		deployments.filter((deployment) => deployment.status === 'stopped')
	);

	async function load(): Promise<void> {
		error = null;
		try {
			deployments = await listDeployments();
		} catch (caught) {
			error = caught instanceof Error ? caught.message : 'Could not load deployments.';
		} finally {
			loading = false;
		}
	}

	async function runLifecycle(id: string, action: 'pause' | 'resume' | 'stop'): Promise<void> {
		if (action === 'stop') {
			const confirmed = window.confirm(
				'Stop this deployment permanently? Resting orders will be canceled.'
			);
			if (!confirmed) return;
		}
		busyId = id;
		actionError = null;
		try {
			if (action === 'pause') await pauseDeployment(id);
			else if (action === 'resume') await resumeDeployment(id);
			else await stopDeployment(id);
			await load();
		} catch (caught) {
			actionError = caught instanceof Error ? caught.message : 'Could not update the deployment.';
		} finally {
			busyId = null;
		}
	}

	function openStrategy(deployment: Deployment): void {
		if (deployment.strategy_id) {
			void goto(resolve(`/strategies/${deployment.strategy_id}`));
		}
	}

	function breakerFlags(deployment: Deployment): string[] {
		const flags: string[] = [];
		if (deployment.daily_loss_latched) flags.push('daily loss breaker latched');
		if (deployment.drawdown_latched) flags.push('drawdown breaker latched');
		return flags;
	}

	onMount(() => {
		void load();
	});
</script>

<svelte:head><title>Deployments · ThyTrader</title></svelte:head>

<main>
	<section class="page-head">
		<div>
			<p class="eyebrow">Runtime status</p>
			<h1>Deployments</h1>
			<p class="lede">
				Everything running on this workstation. Pause, resume, or stop without re-selecting a
				strategy.
			</p>
		</div>
		<a class="link-button" href={resolve('/deploy')}>Start a deployment…</a>
	</section>

	{#if loading}
		<section class="loading-card" aria-label="Loading deployments">
			<div class="skeleton wide"></div>
			<div class="skeleton"></div>
		</section>
	{:else if error}
		<div class="error-banner" role="alert">
			<div>
				<strong>Couldn't load deployments</strong>
				<p>{error}</p>
			</div>
			<button type="button" onclick={() => void load()}>Try again</button>
		</div>
	{:else if deployments.length === 0}
		<section class="empty-state">
			<h2>No deployments yet</h2>
			<p>
				Deploy a published strategy to run it automatically, or place a one-off order on Trade.
				Deployments started here or by an agent appear here.
			</p>
			<a class="link-button" href={resolve('/deploy')}>Open Deploy</a>
		</section>
	{:else}
		{#if actionError}
			<div class="error-banner" role="alert">
				<div>
					<strong>Lifecycle action failed</strong>
					<p>{actionError}</p>
				</div>
			</div>
		{/if}
		{#if running.length > 0}
			<h2 class="group-heading">Running</h2>
			<div class="stack">
				{#each running as deployment (deployment.id)}
					{@render card(deployment)}
				{/each}
			</div>
		{/if}
		{#if paused.length > 0}
			<h2 class="group-heading">Paused</h2>
			<div class="stack">
				{#each paused as deployment (deployment.id)}
					{@render card(deployment)}
				{/each}
			</div>
		{/if}
		{#if attention.length > 0}
			<h2 class="group-heading">Needs attention</h2>
			<div class="stack">
				{#each attention as deployment (deployment.id)}
					{@render card(deployment)}
				{/each}
			</div>
		{/if}
		{#if stoppedItems.length > 0}
			<h2 class="group-heading">Stopped</h2>
			<div class="stack">
				{#each stoppedItems as deployment (deployment.id)}
					{@render card(deployment)}
				{/each}
			</div>
		{/if}
	{/if}
</main>

{#snippet card(deployment: Deployment)}
	<article class="deploy-card">
		<header class="card-head">
			<div class="title">
				<span class="mode mode-{deployment.mode}">{deployment.mode}</span>
				<h2>{deployment.product_id}</h2>
				<span class="meta">{deployment.timeframe ?? '—'} · {deployment.status}</span>
			</div>
			{#if deployment.strategy_id}
				<button class="bar-button" type="button" onclick={() => openStrategy(deployment)}>
					Strategy →
				</button>
			{/if}
		</header>
		<div class="facts">
			<div><span>Lifecycle</span><strong>{deployment.lifecycle_command}</strong></div>
			<div><span>Cash</span><strong>{deployment.cash}</strong></div>
			{#if deployment.last_signal}
				<div><span>Last signal</span><strong>{deployment.last_signal}</strong></div>
			{/if}
			{#if deployment.last_evaluated_bar}
				<div><span>Last bar</span><strong>{deployment.last_evaluated_bar}</strong></div>
			{/if}
		</div>
		{#if canonicalPositions(deployment).length > 0}
			<ul class="positions">
				{#each canonicalPositions(deployment) as position (position.product_id)}
					<li>
						{position.side ?? 'long'}
						{position.quantity}
						{position.product_id} @ {position.entry_price}
						· stop {position.stop_price} · target {position.target_price}
					</li>
				{/each}
			</ul>
		{/if}
		{#if deployment.mismatch_detail}
			<p class="problem" role="alert">{deployment.mismatch_detail}</p>
		{/if}
		{#each breakerFlags(deployment) as flag (flag)}
			<p class="problem" role="status">{flag}</p>
		{/each}
		{#if lifecycleControlsAvailable(deployment)}
			<div class="actions">
				{#if deployment.status === 'running'}
					<button
						class="bar-button"
						type="button"
						disabled={busyId === deployment.id}
						onclick={() => void runLifecycle(deployment.id, 'pause')}
					>
						Pause
					</button>
				{/if}
				{#if deployment.status === 'paused'}
					<button
						class="bar-button"
						type="button"
						disabled={busyId === deployment.id}
						onclick={() => void runLifecycle(deployment.id, 'resume')}
					>
						Resume
					</button>
				{/if}
				{#if deployment.status !== 'stopped'}
					<button
						class="bar-button bar-danger"
						type="button"
						disabled={busyId === deployment.id}
						onclick={() => void runLifecycle(deployment.id, 'stop')}
					>
						Stop…
					</button>
				{/if}
			</div>
		{:else}
			<p class="contract-note">{lifecycleContractNote(deployment)}</p>
		{/if}
	</article>
{/snippet}

<style>
	.page-head {
		display: flex;
		justify-content: space-between;
		align-items: end;
		gap: 18px;
		margin-bottom: 28px;
	}
	.link-button {
		display: inline-block;
		color: #dce4e5;
		background: #151b1d;
		border: 1px solid #303a3c;
		border-radius: 9px;
		padding: 11px 15px;
		text-decoration: none;
		font: inherit;
		font-size: 14px;
	}
	.link-button:hover {
		border-color: #5ce1b5;
	}
	.group-heading {
		margin: 26px 0 12px;
		font-size: 13px;
		color: #778386;
		text-transform: uppercase;
		letter-spacing: 0.07em;
	}
	.stack {
		display: grid;
		gap: 12px;
	}
	.deploy-card {
		border: 1px solid #232b2d;
		background: linear-gradient(145deg, rgba(20, 26, 28, 0.95), rgba(12, 16, 18, 0.95));
		border-radius: 13px;
		padding: 18px 20px;
		display: grid;
		gap: 12px;
	}
	.card-head {
		display: flex;
		justify-content: space-between;
		align-items: center;
		gap: 12px;
	}
	.title {
		display: flex;
		align-items: baseline;
		gap: 10px;
		flex-wrap: wrap;
	}
	.title h2 {
		margin: 0;
		font-size: 18px;
	}
	.meta {
		color: #778386;
		font-size: 12px;
	}
	.mode {
		font:
			600 10px ui-monospace,
			SFMono-Regular,
			Consolas,
			monospace;
		text-transform: uppercase;
		letter-spacing: 0.08em;
		border-radius: 6px;
		padding: 3px 7px;
	}
	.mode-paper {
		color: #9fd9ff;
		border: 1px solid #2c4a5c;
		background: #10222c;
	}
	.mode-live {
		color: #ffb3b3;
		border: 1px solid #733d3d;
		background: #2c1212;
	}
	.facts {
		display: flex;
		flex-wrap: wrap;
		gap: 8px 26px;
	}
	.facts span {
		display: block;
		color: #657174;
		font-size: 10px;
		text-transform: uppercase;
		letter-spacing: 0.07em;
	}
	.facts strong {
		font:
			500 13px ui-monospace,
			SFMono-Regular,
			Consolas,
			monospace;
		color: #dce4e5;
	}
	.positions {
		margin: 0;
		padding: 10px 12px;
		border: 1px solid #223033;
		border-radius: 8px;
		background: #0d1416;
		color: #aeb9bb;
		font:
			400 12px ui-monospace,
			SFMono-Regular,
			Consolas,
			monospace;
	}
	.problem {
		margin: 0;
		color: #f0a3a3;
		font-size: 13px;
	}
	.contract-note {
		margin: 0;
		color: #b39b72;
		font-size: 12px;
	}
	.actions {
		display: flex;
		gap: 8px;
	}
	.bar-button {
		border: 1px solid #303a3c;
		background: #151b1d;
		color: #dce4e5;
		border-radius: 8px;
		padding: 7px 12px;
		font: inherit;
		font-size: 12px;
		cursor: pointer;
	}
	.bar-button:hover:not(:disabled) {
		border-color: #5ce1b5;
	}
	.bar-danger {
		color: #f0a3a3;
		border-color: #5c3232;
	}
	.bar-button:disabled {
		opacity: 0.5;
		cursor: wait;
	}
</style>
