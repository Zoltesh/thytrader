<script lang="ts">
	/**
	 * Why stage: for each deployment of this strategy, the latest
	 * completed-bar signal and the persisted trade reasons
	 * (`GET /api/v1/memory/trade-reasons?deployment_id=`), newest first. Each
	 * card says whether that bot runs the current rules or an earlier edit.
	 *
	 * Joins only what contracts prove: deployments by `strategy_id`,
	 * reasons by deployment id, and an order only when the server-composed
	 * reason names it. Full per-bar decision history is not recorded yet.
	 */
	import { resolve } from '$app/paths';
	import { untrack } from 'svelte';
	import { listStrategyDeployments, type Deployment } from '$lib/deployments';
	import { fetchTradeReasons } from '$lib/memory';
	import { DECISION_HISTORY_NOTE, workspaceHref } from '$lib/strategy-workspace';
	import { sortTradeReasons } from '$lib/trade-reasons';
	import TradeReasonTimeline, { type TradeReasonState } from '$lib/TradeReasonTimeline.svelte';
	import RulesBadge from '$lib/workspace/RulesBadge.svelte';
	import { useWorkspace } from '$lib/workspace/workspace.svelte';

	const workspace = useWorkspace();

	let deployments = $state<Deployment[]>([]);
	let loading = $state(false);
	let loadError = $state<string | null>(null);
	let reasons = $state<Record<string, TradeReasonState>>({});
	let request = 0;

	$effect(() => {
		const key = workspace.strategyId;
		void key;
		untrack(() => void load());
	});

	async function load(): Promise<void> {
		const requestId = ++request;
		deployments = [];
		reasons = {};
		if (workspace.strategyId === '') return;
		loading = true;
		loadError = null;
		try {
			const rows = await listStrategyDeployments(workspace.strategyId);
			if (requestId !== request) return;
			deployments = rows;
			for (const deployment of deployments) void loadReasons(deployment.id, requestId);
		} catch (caught) {
			if (requestId !== request) return;
			loadError = caught instanceof Error ? caught.message : 'Could not load deployments.';
		} finally {
			if (requestId === request) loading = false;
		}
	}

	async function loadReasons(deploymentId: string, requestId: number): Promise<void> {
		reasons = { ...reasons, [deploymentId]: { status: 'loading' } };
		try {
			const records = await fetchTradeReasons({ deploymentId });
			if (requestId !== request) return;
			const sorted = sortTradeReasons(records);
			reasons = { ...reasons, [deploymentId]: { status: 'ready', records: sorted } };
		} catch (caught) {
			if (requestId !== request) return;
			reasons = {
				...reasons,
				[deploymentId]: {
					status: 'error',
					message: caught instanceof Error ? caught.message : 'Why-trade records are unavailable.'
				}
			};
		}
	}
</script>

<svelte:head><title>Why · {workspace.name ?? 'Strategy'} · ThyTrader</title></svelte:head>

<p class="history-note chip" data-testid="decision-history-note">{DECISION_HISTORY_NOTE}</p>

{#if loading && deployments.length === 0}
	<div class="loading-card" aria-busy="true"><div class="skeleton"></div></div>
{:else if loadError}
	<div class="error-banner" role="alert">
		<div>
			<strong>Deployments could not be loaded</strong>
			<p>{loadError}</p>
		</div>
		<button type="button" onclick={() => void load()}>Retry</button>
	</div>
{:else if deployments.length === 0}
	<div class="empty-state">
		<h2>No deployment of this strategy yet</h2>
		<p>Decisions appear once a paper or live deployment of this strategy evaluates bars.</p>
		<a class="btn" href={resolve(workspaceHref(workspace.strategyId, 'run'))}>Go to Run</a>
	</div>
{:else}
	{#each deployments as deployment (deployment.id)}
		<section
			class="card decisions"
			aria-label="Decisions for {deployment.mode} deployment {deployment.id}"
		>
			<div class="card-head">
				<span
					class="chip"
					class:paper={deployment.mode === 'paper'}
					class:live={deployment.mode === 'live'}
					>{deployment.mode === 'live' ? 'LIVE' : 'Paper'}</span
				>
				<h2>Decisions · {deployment.status}</h2>
				<RulesBadge
					fingerprint={deployment.strategy_fingerprint}
					currentFingerprint={workspace.currentFingerprint}
					current={workspace.validModel}
				/>
				<a class="btn ghost" href={resolve(`/deployments/${encodeURIComponent(deployment.id)}`)}
					>Open bot →</a
				>
			</div>
			<TradeReasonTimeline {deployment} reasons={reasons[deployment.id]} />
		</section>
	{/each}
{/if}

<style>
	.history-note {
		height: auto;
		margin: 0 0 var(--space-4);
		padding: 6px 12px;
		white-space: normal;
		border-style: dashed;
	}
	.decisions {
		margin-bottom: var(--space-4);
	}
	.card-head {
		display: flex;
		align-items: center;
		gap: 10px;
		padding: 14px 16px;
		border-bottom: 1px solid var(--line);
	}
	.card-head h2 {
		margin-right: auto;
		text-transform: capitalize;
	}
</style>
