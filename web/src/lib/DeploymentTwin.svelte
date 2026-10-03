<script lang="ts">
	/** Deliberate comparison pairing; confirmation edits metadata only. */
	import { resolve } from '$app/paths';
	import ConfirmDialog from '$lib/ConfirmDialog.svelte';
	import {
		fetchDeploymentTwin,
		linkDeploymentTwin,
		unlinkDeploymentTwin,
		type Deployment,
		type DeploymentTwinLink
	} from '$lib/deployments';
	import { formatUtcTimestamp } from '$lib/time';

	let {
		deployment,
		inventory,
		disabled = false
	}: {
		deployment: Deployment;
		inventory: Deployment[];
		disabled?: boolean;
	} = $props();
	let link = $state<DeploymentTwinLink | null>(null);
	let loaded = $state(false);
	let reading = $state(false);
	let pending = $state(false);
	let error = $state<string | null>(null);
	let selection = $state('');
	let action = $state<'link' | 'unlink' | null>(null);
	let expectedPartner = $state('');
	let generation = 0;

	const candidates = $derived(
		inventory.filter(
			(bot) =>
				bot.id !== deployment.id &&
				bot.mode !== deployment.mode &&
				bot.kind === 'strategy' &&
				!!deployment.strategy_fingerprint &&
				!!bot.strategy_fingerprint &&
				bot.product_id === deployment.product_id &&
				bot.timeframe === deployment.timeframe
		)
	);
	const counterpart = $derived(
		link === null
			? null
			: link.paper_deployment_id === deployment.id
				? link.live_deployment_id
				: link.paper_deployment_id
	);

	$effect(() => {
		const id = deployment.id;
		loaded = false;
		link = null;
		selection = '';
		action = null;
		void load(id);
	});

	async function load(id: string): Promise<void> {
		const requestGeneration = ++generation;
		reading = true;
		try {
			const result = await fetchDeploymentTwin(id);
			if (requestGeneration !== generation || deployment.id !== id) return;
			link = result.twin;
			loaded = true;
			error = null;
		} catch (cause) {
			if (requestGeneration !== generation || deployment.id !== id) return;
			loaded = false;
			error = cause instanceof Error ? cause.message : 'Twin link is unavailable.';
		} finally {
			if (requestGeneration === generation) reading = false;
		}
	}

	function openAction(next: 'link' | 'unlink'): void {
		expectedPartner = next === 'unlink' ? (counterpart ?? '') : selection;
		action = next;
		error = null;
	}

	async function confirm(): Promise<void> {
		if (action === null || pending || disabled || !loaded || !expectedPartner) return;
		const id = deployment.id;
		const operation = action;
		pending = true;
		try {
			const result = await (operation === 'link' ? linkDeploymentTwin : unlinkDeploymentTwin)(
				id,
				expectedPartner
			);
			if (deployment.id !== id) return;
			link = result.twin;
			action = null;
			selection = '';
		} catch (cause) {
			if (deployment.id !== id) return;
			// A timeout can follow a committed write. Require a fresh read before another attempt.
			loaded = false;
			action = null;
			error = `${cause instanceof Error ? cause.message : 'Request failed.'} Refresh the link before trying again.`;
		} finally {
			pending = false;
		}
	}
</script>

{#if deployment.kind === 'strategy' && deployment.strategy_fingerprint}
	<section class="twin-card" data-testid="deployment-twin" aria-label="Paper/live twin">
		<div class="heading">
			<h2>Paper/live twin</h2>
			<span>Comparison pairing</span>
		</div>
		{#if error}<p class="problem" role="alert">{error}</p>{/if}
		{#if !loaded}
			<p>{reading ? 'Loading comparison link…' : 'Comparison link unavailable.'}</p>
			<button class="btn" disabled={reading || pending} onclick={() => void load(deployment.id)}
				>Refresh link</button
			>
		{:else if counterpart !== null && link !== null}
			<p>
				<a href={resolve(`/deployments/${counterpart}`)}
					>{deployment.mode === 'paper' ? 'Live' : 'Paper'} twin · {counterpart.slice(-8)}</a
				>
				<span class="muted"> · Linked {formatUtcTimestamp(link.linked_at)}</span>
			</p>
			<button class="btn" disabled={disabled || pending} onclick={() => openAction('unlink')}
				>Unlink twin…</button
			>
		{:else}
			<p>
				No twin linked. Choose the opposite-mode bot running this rules snapshot to compare entry
				fills.
			</p>
			{#if candidates.length > 0}
				<div class="controls">
					<label
						>Comparison bot
						<select bind:value={selection} disabled={disabled || pending}>
							<option value="">Choose a bot</option>
							{#each candidates as bot (bot.id)}
								<option value={bot.id}
									>{bot.mode === 'live' ? 'Live' : 'Paper'} · {bot.strategy_name ?? bot.product_id} ·
									{bot.status} · {formatUtcTimestamp(bot.created_at)} · {bot.id.slice(-8)}</option
								>
							{/each}
						</select>
					</label>
					<button
						class="btn"
						disabled={disabled || pending || !selection}
						onclick={() => openAction('link')}>Link twin…</button
					>
				</div>
			{:else}
				<p class="muted">No matching opposite-mode strategy bot is available.</p>
			{/if}
		{/if}
		<p class="muted">
			Links choose comparison partners. They do not start, stop, or arm either bot. The server
			verifies identical snapshotted trading rules, including across clones.
		</p>
	</section>
{/if}

<ConfirmDialog
	open={action !== null}
	title={action === 'unlink' ? 'Unlink paper/live twins?' : 'Link paper/live twins?'}
	confirmLabel={action === 'unlink' ? 'Unlink twins' : 'Link twins'}
	pendingLabel="Saving link…"
	{pending}
	confirmDisabled={disabled || !loaded}
	error={null}
	testId="twin-dialog"
	oncancel={() => {
		action = null;
	}}
	onconfirm={() => void confirm()}
>
	<p>
		{action === 'unlink'
			? 'Remove this saved comparison pairing.'
			: 'Use these two bots for paper/live entry-fill comparisons.'}
	</p>
	<p>Bot {deployment.id} · Counterpart {expectedPartner}</p>
	<p>This changes comparison metadata. Trading state and orders stay unchanged.</p>
</ConfirmDialog>

<style>
	.twin-card {
		border: 1px solid var(--line);
		border-radius: var(--radius-md);
		padding: 1rem;
		margin: 1rem 0;
		background: var(--surface);
	}
	.heading {
		display: flex;
		gap: 1rem;
		align-items: baseline;
		flex-wrap: wrap;
	}
	h2 {
		font-size: 1rem;
		margin: 0;
	}
	.heading span,
	.muted {
		color: var(--muted);
		font-size: 0.85rem;
	}
	.controls {
		display: flex;
		align-items: end;
		gap: 0.75rem;
		flex-wrap: wrap;
	}
	label {
		display: grid;
		gap: 0.35rem;
		font-size: 0.85rem;
	}
	select {
		max-width: 100%;
		padding: 0.5rem;
		border: 1px solid var(--line);
		border-radius: var(--radius-md);
		background: var(--surface);
		color: var(--text);
	}
	.problem {
		color: var(--neg);
	}
</style>
