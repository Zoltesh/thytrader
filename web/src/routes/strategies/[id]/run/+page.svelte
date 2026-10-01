<script lang="ts">
	/**
	 * Run stage: Paper and Live cards for this strategy (ADR 0082).
	 *
	 * Deployments are listed by `strategy_id`; each row says whether it runs the
	 * current rules or an earlier edit, with a guided "Update bot" (managed stop,
	 * then a new start of the current rules). Starting always snapshots the
	 * current saved definition and is blocked while it is invalid. Pause / resume / stop / flatten go through the
	 * accessible lifecycle dialog (managed stop vs `?flatten=true`). Arming live
	 * needs an explicit "real orders" checkbox before `i_understand_live: true`
	 * is sent. The live preflight lists what existing endpoints report, with
	 * `Unknown` where a source cannot be read; it is never a readiness verdict.
	 */
	import { resolve } from '$app/paths';
	import { untrack } from 'svelte';
	import ConfirmDialog from '$lib/ConfirmDialog.svelte';
	import DeploymentLifecycleDialog from '$lib/DeploymentLifecycleDialog.svelte';
	import { fetchCoinbaseCredentialsStatus } from '$lib/credentials';
	import { declareLiveContext } from '$lib/live-context.svelte';
	import {
		lifecycleAcceptedMessage,
		marketLabel,
		type LifecycleAction
	} from '$lib/deployment-detail';
	import {
		createDeployment,
		listStrategyDeployments,
		pauseDeployment,
		resumeDeployment,
		stopDeployment,
		type Deployment
	} from '$lib/deployments';
	import {
		invalidStartReason,
		livePreflight,
		paperEvidenceText,
		timeframeMinutes,
		workspaceHref,
		type LivePreflightInput,
		type PreflightItem
	} from '$lib/strategy-workspace';
	import {
		fetchPortfolioSnapshot,
		fetchRiskPolicySnapshot,
		fetchUserOrderFeed
	} from '$lib/workspace-data';
	import DeploymentRuntimeRow from '$lib/workspace/DeploymentRuntimeRow.svelte';
	import DataReadinessPanel from '$lib/workspace/DataReadinessPanel.svelte';
	import PaperStartForm from '$lib/workspace/PaperStartForm.svelte';
	import { listDatasets, type Dataset } from '$lib/strategies';
	import { useWorkspace } from '$lib/workspace/workspace.svelte';

	const workspace = useWorkspace();

	let deployments = $state<Deployment[]>([]);
	let loading = $state(false);
	let loadError = $state<string | null>(null);
	let loadRequest = 0;

	let dialogAction = $state<LifecycleAction | null>(null);
	let dialogTarget = $state<Deployment | null>(null);
	let stopWithFlatten = $state(false);
	let mutating = $state(false);
	let actionError = $state<string | null>(null);
	let outcomeUnknown = $state(false);
	let acceptedMessage = $state<string | null>(null);

	let preflight = $state<PreflightItem[] | null>(null);
	let liveOpen = $state(false);
	let liveAcknowledged = $state(false);
	let arming = $state(false);
	let armError = $state<string | null>(null);

	const model = $derived(workspace.validModel);
	const fingerprint = $derived(workspace.currentFingerprint ?? '');
	const canStart = $derived(model !== null && fingerprint !== '');
	const paper = $derived(deployments.filter((deployment) => deployment.mode === 'paper'));
	const live = $derived(deployments.filter((deployment) => deployment.mode === 'live'));
	const activePaper = $derived(paper.filter((deployment) => deployment.status !== 'stopped'));
	const activeLive = $derived(live.filter((deployment) => deployment.status !== 'stopped'));
	const controlsBlocked = $derived(mutating || outcomeUnknown);

	/** Latest verified datasets, used to name clocks the worker does not cover yet. */
	let runDatasets = $state<Dataset[] | null>(null);
	let datasetRequest = 0;

	async function loadRunDatasets(): Promise<void> {
		const requestId = ++datasetRequest;
		try {
			const datasets = await listDatasets();
			if (requestId === datasetRequest) runDatasets = datasets;
		} catch {
			/* readiness stays unknown; starting is still gated server-side */
			if (requestId === datasetRequest) runDatasets = null;
		}
	}

	$effect(() => {
		const key = workspace.strategyId;
		void key;
		untrack(() => {
			deployments = [];
			void loadDeployments();
		});
	});

	$effect(() => {
		// Live chrome while the arm-live confirmation is on screen.
		if (!liveOpen) return;
		return declareLiveContext({ kind: 'arm', productId: model?.product_id ?? null, cap: null });
	});

	$effect(() => {
		const current = model;
		if (current === null) return;
		untrack(() => void loadPreflight(current.product_id, current.timeframe));
	});

	$effect(() => {
		const key = workspace.strategyId;
		void key;
		untrack(() => void loadRunDatasets());
	});

	async function loadDeployments(): Promise<boolean> {
		const requestId = ++loadRequest;
		const strategyId = workspace.strategyId;
		loading = true;
		loadError = null;
		try {
			const all = await listStrategyDeployments(strategyId);
			if (requestId !== loadRequest) return false;
			deployments = all;
			return true;
		} catch (caught) {
			if (requestId !== loadRequest) return false;
			loadError = caught instanceof Error ? caught.message : 'Could not load deployments.';
			return false;
		} finally {
			if (requestId === loadRequest) loading = false;
		}
	}

	async function settle<T>(read: () => Promise<T>): Promise<T | { unreadable: true }> {
		try {
			return await read();
		} catch {
			return { unreadable: true };
		}
	}

	async function loadPreflight(productId: string, timeframe: string): Promise<void> {
		preflight = null;
		const minutes = timeframeMinutes(timeframe);
		const [credentials, riskPolicy, portfolio, userOrderFeed] = await Promise.all([
			settle(fetchCoinbaseCredentialsStatus),
			settle(fetchRiskPolicySnapshot),
			settle(fetchPortfolioSnapshot),
			minutes !== null && minutes >= 60 ? Promise.resolve(null) : settle(fetchUserOrderFeed)
		]);
		const input: LivePreflightInput = {
			strategyId: workspace.strategyId,
			productId,
			timeframe,
			credentials,
			riskPolicy,
			portfolio,
			userOrderFeed
		};
		preflight = livePreflight(input);
	}

	function openLifecycle(action: LifecycleAction, deployment: Deployment): void {
		dialogAction = action;
		dialogTarget = deployment;
		stopWithFlatten = false;
		actionError = null;
	}

	function closeLifecycle(): void {
		if (mutating) return;
		dialogAction = null;
		dialogTarget = null;
	}

	async function confirmLifecycle(options: { liveAcknowledged: boolean }): Promise<void> {
		const action = dialogAction;
		const target = dialogTarget;
		if (action === null || target === null || mutating) return;
		mutating = true;
		actionError = null;
		acceptedMessage = null;
		try {
			let updated: Deployment;
			if (action === 'pause') updated = await pauseDeployment(target.id);
			else if (action === 'resume')
				// The flag comes from the dialog's ticked "real orders" checkbox, never from mode alone.
				updated = await resumeDeployment(target.id, {
					liveAcknowledged: target.mode === 'live' && options.liveAcknowledged
				});
			else if (action === 'flatten') updated = await stopDeployment(target.id, true);
			else updated = await stopDeployment(target.id, stopWithFlatten);
			// Merge the mutation response first, then refresh.
			deployments = deployments.map((deployment) =>
				deployment.id === updated.id ? updated : deployment
			);
			acceptedMessage = lifecycleAcceptedMessage(
				stopWithFlatten && action === 'stop' ? 'flatten' : action
			);
			dialogAction = null;
			dialogTarget = null;
			const refreshed = await loadDeployments();
			if (!refreshed) {
				acceptedMessage = `${acceptedMessage} The latest snapshot could not be refreshed; retry before taking another action.`;
			}
		} catch (caught) {
			const message = caught instanceof Error ? caught.message : 'The action could not be sent.';
			if (message === 'Failed to fetch' || message.includes('NetworkError')) {
				outcomeUnknown = true;
				dialogAction = null;
				dialogTarget = null;
				actionError =
					'Outcome unknown. The request may or may not have been applied. Refresh before retrying the action.';
			} else {
				actionError = message;
			}
		} finally {
			mutating = false;
		}
	}

	async function retryRefresh(): Promise<void> {
		if (await loadDeployments()) {
			outcomeUnknown = false;
			actionError = null;
		}
	}

	function openLive(): void {
		liveAcknowledged = false;
		armError = null;
		liveOpen = true;
	}

	async function armLive(): Promise<void> {
		if (!liveAcknowledged || arming || fingerprint === '') return;
		arming = true;
		armError = null;
		try {
			// Reached only after the "I understand this places real orders" checkbox.
			const created = await createDeployment({
				strategy_id: workspace.strategyId,
				mode: 'live',
				i_understand_live: true
			});
			deployments = [...deployments, created];
			liveOpen = false;
			acceptedMessage =
				'Live deployment armed. It places real Coinbase orders from the next eligible completed bar.';
			void loadDeployments();
		} catch (caught) {
			armError = caught instanceof Error ? caught.message : 'Could not arm live trading.';
		} finally {
			arming = false;
		}
	}

	function onBotUpdated(result: { stopped: Deployment; started: Deployment | null }): void {
		deployments = deployments.map((deployment) =>
			deployment.id === result.stopped.id ? result.stopped : deployment
		);
		if (result.started !== null) {
			deployments = [...deployments, result.started];
			acceptedMessage =
				'Bot updated: the earlier bot got a managed stop and a new bot started with the current rules.';
		}
		void loadDeployments();
	}

	function onPaperStarted(deployment: Deployment): void {
		deployments = [...deployments, deployment];
		acceptedMessage = 'Paper deployment started.';
		void loadDeployments();
	}
</script>

<svelte:head><title>Run · {workspace.name ?? 'Strategy'} · ThyTrader</title></svelte:head>

{#if workspace.record}
	{#if !canStart}
		<div class="blocked" role="status" data-testid="run-blocked-invalid">
			<div>
				<strong>Starting bots is blocked</strong>
				<p>{invalidStartReason(workspace.issues.length)}</p>
			</div>
			<a class="btn" href={resolve(workspaceHref(workspace.strategyId, 'build'))}>Go to Build</a>
		</div>
	{/if}
	{#if acceptedMessage}<p class="accepted" role="status">{acceptedMessage}</p>{/if}
	{#if actionError && dialogAction === null}
		<div class="error-banner" role="alert">
			<div>
				<strong>{outcomeUnknown ? 'Outcome unknown' : 'Action failed'}</strong>
				<p>{actionError}</p>
			</div>
			{#if outcomeUnknown}<button type="button" onclick={() => void retryRefresh()}>Refresh</button
				>{/if}
		</div>
	{/if}
	{#if loadError}
		<div class="error-banner" role="alert">
			<div>
				<strong>Deployments could not be loaded</strong>
				<p>{loadError}</p>
			</div>
			<button type="button" onclick={() => void loadDeployments()}>Retry</button>
		</div>
	{/if}
	{#if model && canStart}
		<DataReadinessPanel
			strategyId={workspace.strategyId}
			{model}
			datasets={runDatasets}
			context="run"
			onRefresh={() => void loadRunDatasets()}
		/>
	{/if}
	<div class="run-grid">
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
				{#if loading && deployments.length === 0}
					<div class="skeleton"></div>
				{:else}
					{#each paper as deployment (deployment.id)}
						<DeploymentRuntimeRow
							{deployment}
							disabled={controlsBlocked}
							currentFingerprint={workspace.currentFingerprint}
							current={model}
							onaction={openLifecycle}
							onupdated={onBotUpdated}
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
								strategyId={workspace.strategyId}
								currentFingerprint={fingerprint}
								name={workspace.name ?? model.name}
								{model}
								disabled={controlsBlocked}
								onStarted={onPaperStarted}
							/>
						</details>
					{:else}
						<h3 class="sub">Start paper with the current rules</h3>
						<PaperStartForm
							strategyId={workspace.strategyId}
							currentFingerprint={fingerprint}
							name={workspace.name ?? model.name}
							{model}
							disabled={controlsBlocked}
							onStarted={onPaperStarted}
						/>
					{/if}
				{/if}
			</div>
		</section>

		<section class="card run-card live-card" aria-labelledby="live-title" data-testid="live-card">
			<div class="card-head">
				<span class="chip live">LIVE</span>
				<h2 id="live-title">
					{activeLive.length > 0 ? `${activeLive.length} running or paused` : 'Not running'}
				</h2>
			</div>
			<div class="card-body">
				{#each live as deployment (deployment.id)}
					<DeploymentRuntimeRow
						{deployment}
						disabled={controlsBlocked}
						currentFingerprint={workspace.currentFingerprint}
						current={model}
						onaction={openLifecycle}
						onupdated={onBotUpdated}
					/>
				{/each}
				<h3 class="sub">Live preflight</h3>
				<p class="note">
					What existing endpoints report right now. Items are independent facts, not a readiness
					verdict; arming stays your decision.
				</p>
				<ul class="checks" aria-label="Live preflight" aria-busy={preflight === null}>
					{#if preflight === null}
						<li class="check"><span class="faint">…</span>Reading preflight sources…</li>
					{:else}
						{#each preflight as item (item.id)}
							<li class="check" data-preflight={item.id} data-state={item.state}>
								<span class="mark {item.state}" aria-hidden="true"
									>{item.state === 'ok' ? '✓' : item.state === 'attention' ? '!' : '?'}</span
								>
								<span
									><span class="sr-only"
										>{item.state === 'ok'
											? 'Reported:'
											: item.state === 'attention'
												? 'Needs attention:'
												: 'Unknown:'}</span
									>
									{item.label}</span
								>
							</li>
						{/each}
					{/if}
					<li class="check" data-preflight="paper-evidence">
						<span class="mark info" aria-hidden="true">i</span>
						<span class="muted">{paperEvidenceText(paper)}</span>
					</li>
				</ul>
				<button
					class="btn live arm"
					type="button"
					disabled={controlsBlocked || !canStart}
					aria-describedby={canStart ? undefined : 'arm-blocked'}
					onclick={openLive}>Arm live trading…</button
				>
				{#if !canStart}<p class="note" id="arm-blocked">
						Blocked until the saved definition is valid.
					</p>{/if}
			</div>
		</section>
	</div>

	<DeploymentLifecycleDialog
		deployment={dialogTarget}
		action={dialogAction}
		bind:stopWithFlatten
		{mutating}
		{actionError}
		requireLiveAcknowledgement
		oncancel={closeLifecycle}
		onconfirm={(options) => void confirmLifecycle(options)}
	/>

	<ConfirmDialog
		open={liveOpen}
		title="Arm live trading?"
		tone="live"
		confirmLabel="Arm live trading"
		pendingLabel="Arming…"
		pending={arming}
		confirmDisabled={!liveAcknowledged}
		confirmDisabledReason="Tick the acknowledgement to continue."
		error={armError}
		testId="live-arm-dialog"
		oncancel={() => (liveOpen = false)}
		onconfirm={() => void armLive()}
	>
		<p>
			This starts a live deployment of the current rules of
			<strong>{workspace.name ?? model?.name ?? 'this strategy'}</strong>
			that places real spot orders on Coinbase with your API keys. Later edits do not change it. You are
			responsible for every trade and its market risk.
		</p>
		{#if model}
			<div class="row">
				<span>Market</span><span>{marketLabel(model.product_id)} · {model.timeframe}</span>
			</div>
			<div class="row"><span>Coinbase product record</span><code>{model.product_id}</code></div>
		{/if}
		<div class="row"><span>Rules snapshot</span><code class="fp">{fingerprint}</code></div>
		<div class="row">
			<span>If you stop it</span><span
				>Managed stop keeps protective exits until flat; flatten is a separate choice</span
			>
		</div>
		<label class="live-ack">
			<input type="checkbox" bind:checked={liveAcknowledged} />
			<span>I understand this places real orders on Coinbase with real money.</span>
		</label>
	</ConfirmDialog>
{:else}
	<div class="loading-card" aria-busy="true"><div class="skeleton"></div></div>
{/if}

<style>
	.run-grid {
		display: grid;
		grid-template-columns: repeat(2, minmax(0, 1fr));
		gap: var(--space-4);
		align-items: start;
	}
	.live-card {
		border-color: var(--live);
	}
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
	.note {
		margin: 0;
		color: var(--faint);
		font-size: var(--fs-sm);
	}
	.muted {
		margin: 0;
		color: var(--muted);
	}
	.faint {
		color: var(--faint);
	}
	.checks {
		margin: 0;
		padding: 0;
		list-style: none;
	}
	.check {
		display: flex;
		align-items: flex-start;
		gap: 10px;
		padding: 9px 0;
		border-bottom: 1px solid var(--line);
	}
	.check:last-child {
		border-bottom: 0;
	}
	.mark {
		flex: none;
		width: 16px;
		font-weight: 700;
		text-align: center;
	}
	.mark.ok {
		color: var(--pos);
	}
	.mark.attention {
		color: var(--warn);
	}
	.mark.unknown,
	.mark.info {
		color: var(--faint);
	}
	.arm {
		width: 100%;
		margin-top: 6px;
	}
	.start-another summary {
		color: var(--muted);
		cursor: pointer;
	}
	.start-another[open] summary {
		margin-bottom: 10px;
	}
	.accepted {
		margin: 0 0 var(--space-3);
		color: var(--pos);
	}
	.blocked {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 16px;
		margin-bottom: var(--space-4);
		padding: 14px 17px;
		border: 1px solid var(--warn-line);
		border-radius: var(--radius-lg);
		background: var(--surface-2);
	}
	.blocked p {
		margin: 4px 0 0;
		color: var(--muted);
	}
	.live-ack {
		display: flex;
		align-items: flex-start;
		gap: 10px;
		margin-top: 6px;
		padding: 12px;
		border-radius: var(--radius-md);
		background: var(--live-soft);
		color: var(--text);
		cursor: pointer;
	}
	.fp {
		font-size: var(--fs-xs);
		word-break: break-all;
	}
	@media (max-width: 1100px) {
		.run-grid {
			grid-template-columns: minmax(0, 1fr);
		}
	}
</style>
