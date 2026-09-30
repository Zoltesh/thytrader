<script lang="ts">
	/**
	 * Why stage: for each deployment of the selected exact version, the
	 * latest completed-bar signal and the persisted trade reasons
	 * (`GET /api/v1/memory/trade-reasons?deployment_id=`), newest first.
	 *
	 * Joins only what contracts prove: deployments by exact fingerprint,
	 * reasons by deployment id, and an order only when the server-composed
	 * reason names it. Full per-bar decision history is not recorded yet.
	 */
	import { resolve } from '$app/paths';
	import { untrack } from 'svelte';
	import { listAllDeployments, type Deployment } from '$lib/deployments';
	import { fetchTradeReasons, type TradeReasonRecord } from '$lib/memory';
	import {
		DECISION_HISTORY_NOTE,
		latestSignalExplanation,
		workspaceHref
	} from '$lib/strategy-workspace';
	import { formatUtcTimestamp } from '$lib/time';
	import { useWorkspace } from '$lib/workspace/workspace.svelte';

	const workspace = useWorkspace();

	type ReasonState =
		| { status: 'loading' }
		| { status: 'error'; message: string }
		| { status: 'ready'; records: TradeReasonRecord[] };

	let deployments = $state<Deployment[]>([]);
	let loading = $state(false);
	let loadError = $state<string | null>(null);
	let reasons = $state<Record<string, ReasonState>>({});
	let request = 0;

	const selected = $derived(workspace.version.entry);
	const fingerprint = $derived(selected?.strategy_fingerprint ?? '');

	$effect(() => {
		const key = `${workspace.strategyId}:${fingerprint}`;
		void key;
		untrack(() => void load());
	});

	async function load(): Promise<void> {
		const requestId = ++request;
		deployments = [];
		reasons = {};
		if (fingerprint === '') return;
		loading = true;
		loadError = null;
		try {
			const all = await listAllDeployments();
			if (requestId !== request) return;
			deployments = all.filter(
				(deployment) =>
					deployment.strategy_id === workspace.strategyId &&
					deployment.strategy_fingerprint === fingerprint
			);
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
			const sorted = [...records].sort((a, b) => b.created_at.localeCompare(a.created_at));
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

	function reconcileText(record: TradeReasonRecord): string {
		const { reconcile } = record;
		if (!reconcile.ledger_available)
			return 'Execution ledger unavailable; order outcome cannot be reconciled.';
		if (reconcile.unknown_timeout)
			return 'Submission timed out; order outcome unknown until reconciled.';
		if (reconcile.order_id === null || reconcile.order_status === null)
			return 'Intent recorded; no venue-visible order found.';
		const fills = reconcile.fills.length;
		return `Order ${reconcile.order_id.slice(0, 8)} · ${reconcile.order_status} · ${fills} fill${fills === 1 ? '' : 's'}${reconcile.reject_reason ? ` · rejected: ${reconcile.reject_reason}` : ''}`;
	}

	function kindLabel(record: TradeReasonRecord): string {
		switch (record.signal.kind) {
			case 'strategy_entry':
				return 'Entry';
			case 'take_profit':
				return 'Take profit';
			case 'stop':
				return 'Stop';
			case 'time_exit':
				return 'Time exit';
			default:
				return record.signal.kind.replace('_', ' ');
		}
	}
</script>

<svelte:head><title>Why · {workspace.name ?? 'Strategy'} · ThyTrader</title></svelte:head>

<p class="history-note chip" data-testid="decision-history-note">{DECISION_HISTORY_NOTE}</p>

{#if workspace.version.status === 'none'}
	<div class="empty-state">
		<h2>No published version yet</h2>
		<p>Decisions exist only for deployments of a published version.</p>
	</div>
{:else if loading && deployments.length === 0}
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
		<h2>No deployment uses v{selected?.version}</h2>
		<p>Decisions appear once a paper or live deployment of this exact version evaluates bars.</p>
		<a
			class="btn"
			href={resolve(
				workspaceHref(workspace.strategyId, 'run', { version: workspace.requestedVersion })
			)}>Go to Run</a
		>
	</div>
{:else}
	{#each deployments as deployment (deployment.id)}
		{@const signal = latestSignalExplanation(deployment)}
		{@const reason = reasons[deployment.id]}
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
				<a class="btn ghost" href={resolve(`/deployments/${encodeURIComponent(deployment.id)}`)}
					>Open bot →</a
				>
			</div>
			<div class="tl latest" data-testid="latest-signal" data-kind={signal.kind}>
				<div class="t mono">
					{deployment.last_evaluated_bar ? formatUtcTimestamp(deployment.last_evaluated_bar) : '—'}
				</div>
				<div>
					<div class="title">{signal.title}</div>
					<div class="muted">{signal.detail}</div>
				</div>
			</div>
			{#if reason === undefined || reason.status === 'loading'}
				<div class="tl">
					<div class="t"></div>
					<div class="muted">Loading trade reasons…</div>
				</div>
			{:else if reason.status === 'error'}
				<div class="tl" role="alert">
					<div class="t"></div>
					<div class="problem">{reason.message}</div>
				</div>
			{:else if reason.records.length === 0}
				<div class="tl" data-testid="no-trade-reasons">
					<div class="t"></div>
					<div>
						<div class="title">No recorded trade rationale for this deployment</div>
						<div class="muted">
							This can mean no intent was persisted, or rationale recording was unavailable. Orders
							cannot be matched to intents from the deployment response.
						</div>
					</div>
				</div>
			{:else}
				{#each reason.records as record (record.id)}
					<div class="tl" data-testid="trade-reason">
						<div class="t mono">{formatUtcTimestamp(record.signal.candle_starts_at)}</div>
						<div>
							<div class="title">
								<span class:pos={record.signal.kind === 'strategy_entry'}>{kindLabel(record)}</span>
								· {record.side}
								{record.product_id} · risk {record.risk.decision} ({record.risk.reason_code})
							</div>
							<div class="muted">
								Why this intent was persisted: signal {record.signal.last_signal ??
									record.signal.kind}
								on the completed bar · {record.origin} · {reconcileText(record)}
							</div>
							<div class="faint">
								{record.notes.length === 0
									? 'No operator note.'
									: record.notes.map((note) => `${note.origin}: ${note.body}`).join(' · ')}
							</div>
						</div>
					</div>
				{/each}
			{/if}
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
	.tl {
		display: flex;
		gap: 14px;
		padding: 12px 16px;
		border-bottom: 1px solid var(--line);
	}
	.tl:last-child {
		border-bottom: 0;
	}
	.t {
		flex: none;
		width: 150px;
		color: var(--faint);
		font-size: var(--fs-sm);
	}
	.title {
		font-weight: 500;
	}
	.muted {
		color: var(--muted);
	}
	.faint {
		color: var(--faint);
		font-size: var(--fs-sm);
	}
	.pos {
		color: var(--pos);
	}
	.problem {
		color: var(--neg);
	}
	.latest {
		background: var(--surface-2);
	}
</style>
