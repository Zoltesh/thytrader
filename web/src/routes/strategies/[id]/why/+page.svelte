<script lang="ts">
	/**
	 * Why stage: the durable per-bar decision timeline of this strategy's bots
	 * (`GET /api/v1/strategies/{id}/decisions`), newest first, for all bots or
	 * one selected bot (`deployment_id`). Each bot in the selector says whether
	 * it runs the current rules or an earlier edit, its latest completed-bar
	 * signal, and when it next evaluates.
	 *
	 * Joins only what contracts prove: deployments by `strategy_id`, trade
	 * reasons by deployment id, and each reason onto its decision row by
	 * `intent_id` (never shown twice).
	 */
	import { resolve } from '$app/paths';
	import { untrack } from 'svelte';
	import DecisionTimeline from '$lib/DecisionTimeline.svelte';
	import { DECISION_RETENTION_NOTE, decisionBotLabel, nextEvaluationText } from '$lib/decisions';
	import { marketLabel } from '$lib/deployment-detail';
	import { canonicalBooks, listStrategyDeployments, type Deployment } from '$lib/deployments';
	import { fetchTradeReasons } from '$lib/memory';
	import { latestSignalExplanation, workspaceHref } from '$lib/strategy-workspace';
	import {
		mergeTradeReasonStates,
		sortTradeReasons,
		type TradeReasonState
	} from '$lib/trade-reasons';
	import RulesBadge from '$lib/workspace/RulesBadge.svelte';
	import { useWorkspace } from '$lib/workspace/workspace.svelte';

	const workspace = useWorkspace();

	let deployments = $state<Deployment[]>([]);
	let loading = $state(false);
	let loadError = $state<string | null>(null);
	let reasons = $state<Record<string, TradeReasonState>>({});
	/** Selected bot; null shows every bot of this strategy. */
	let selected = $state<string | null>(null);
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
		selected = null;
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

	const selectedDeployment = $derived(
		selected === null ? null : (deployments.find((item) => item.id === selected) ?? null)
	);
	/** Bots whose decisions are on screen. */
	const shown = $derived(selectedDeployment === null ? deployments : [selectedDeployment]);
	const shownReasons = $derived(mergeTradeReasonStates(shown.map((item) => reasons[item.id])));
	const multiProduct = $derived(
		shown.some((item) => canonicalBooks(item).length > 1) ||
			new Set(shown.map((item) => item.product_id)).size > 1
	);
	const labels = $derived(new Map(deployments.map((item) => [item.id, decisionBotLabel(item)])));
	/** Rows name their bot only while several bots share the timeline. */
	const botLabel = $derived(
		shown.length > 1 ? (deploymentId: string) => labels.get(deploymentId) ?? null : undefined
	);
	const timeframe = $derived(
		shown.length > 0 && shown.every((item) => item.timeframe === shown[0]?.timeframe)
			? (shown[0]?.timeframe ?? null)
			: null
	);
</script>

<svelte:head><title>Why · {workspace.name ?? 'Strategy'} · ThyTrader</title></svelte:head>

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
	<section class="card bots" aria-labelledby="why-bots-title">
		<div class="card-head">
			<h2 id="why-bots-title">Bots</h2>
			<span class="faint">Choose whose decisions to show</span>
		</div>
		<ul class="bot-list" data-testid="decision-deployment-selector">
			<li class="bot" class:on={selected === null}>
				<button
					type="button"
					class="pick"
					aria-pressed={selected === null}
					data-testid="decision-deployment-option"
					data-deployment-id=""
					onclick={() => (selected = null)}
					>All bots <span class="count">{deployments.length}</span></button
				>
			</li>
			{#each deployments as deployment (deployment.id)}
				{@const signal = latestSignalExplanation(deployment)}
				{@const isLive = deployment.mode === 'live'}
				<li
					class="bot"
					class:on={selected === deployment.id}
					data-testid="decision-deployment"
					data-deployment-id={deployment.id}
				>
					<button
						type="button"
						class="pick"
						aria-pressed={selected === deployment.id}
						data-testid="decision-deployment-option"
						data-deployment-id={deployment.id}
						onclick={() => (selected = deployment.id)}
					>
						<span class="chip" class:paper={!isLive} class:live={isLive}
							>{isLive ? 'LIVE' : 'Paper'}</span
						>
						<span class="bot-name"
							>{marketLabel(deployment.product_id)} · {deployment.timeframe ?? 'clock unknown'} · {deployment.status}
							· since {deployment.created_at.slice(0, 10)}</span
						>
					</button>
					<RulesBadge
						fingerprint={deployment.strategy_fingerprint}
						currentFingerprint={workspace.currentFingerprint}
						current={workspace.validModel}
					/>
					<span class="bot-facts">
						<span data-testid="latest-signal" data-kind={signal.kind}
							>Latest bar: {signal.title}</span
						>
						<span data-testid="bot-next-evaluation">{nextEvaluationText(deployment)}</span>
					</span>
					<a
						class="btn ghost"
						href={resolve(`/deployments/${encodeURIComponent(deployment.id)}`)}
						aria-label="Open {isLive ? 'live' : 'paper'} bot {deployment.id}">Open bot →</a
					>
				</li>
			{/each}
		</ul>
	</section>

	<section class="card decisions" aria-labelledby="why-decisions-title" data-testid="why-decisions">
		<div class="card-head">
			<h2 id="why-decisions-title">Decisions</h2>
			<span class="faint" data-testid="why-decisions-scope"
				>{selectedDeployment === null
					? `All ${deployments.length} bot${deployments.length === 1 ? '' : 's'} of this strategy`
					: decisionBotLabel(selectedDeployment)}</span
			>
		</div>
		<DecisionTimeline
			source={{ kind: 'strategy', strategyId: workspace.strategyId, deploymentId: selected }}
			reasons={shownReasons}
			{multiProduct}
			deploymentLabel={botLabel}
			{timeframe}
		/>
		<p class="retention-note" data-testid="decision-retention-note">{DECISION_RETENTION_NOTE}</p>
	</section>
{/if}

<style>
	.bots,
	.decisions {
		margin-bottom: var(--space-4);
	}
	.card-head {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 4px 10px;
		padding: 14px 16px;
		border-bottom: 1px solid var(--line);
	}
	.card-head h2 {
		margin-right: auto;
	}
	.faint {
		color: var(--faint);
		font-size: var(--fs-sm);
	}
	.bot-list {
		display: grid;
		margin: 0;
		padding: 0;
		list-style: none;
	}
	.bot {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 6px 12px;
		padding: 10px 16px;
		border-bottom: 1px solid var(--line);
	}
	.bot:last-child {
		border-bottom: 0;
	}
	.bot.on {
		background: var(--surface-2);
		box-shadow: inset 2px 0 0 var(--accent);
	}
	/* RulesBadge's "What changed" diff opens below the bot's row, full width. */
	.bot > :global(.diff-panel) {
		flex: 1 0 100%;
	}
	.pick {
		display: inline-flex;
		align-items: center;
		gap: 8px;
		min-height: 30px;
		padding: 0 10px;
		border: 1px solid var(--line-2);
		border-radius: var(--radius-md);
		background: var(--surface);
		color: var(--text);
		font-weight: 500;
		text-align: left;
		cursor: pointer;
	}
	.pick:hover {
		background: var(--hover);
	}
	.pick[aria-pressed='true'] {
		border-color: var(--accent);
		background: var(--accent-soft);
	}
	.count {
		color: var(--muted);
		font-size: var(--fs-xs);
	}
	.bot-name {
		overflow-wrap: anywhere;
	}
	.bot-facts {
		display: grid;
		flex: 1;
		min-width: 200px;
		color: var(--muted);
		font-size: var(--fs-sm);
	}
	.retention-note {
		margin: 0;
		padding: 10px 16px;
		border-top: 1px dashed var(--line-2);
		color: var(--faint);
		font-size: var(--fs-sm);
	}
</style>
