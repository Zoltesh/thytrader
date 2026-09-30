<script lang="ts">
	/**
	 * Run stage: Paper and Live cards for the selected exact version.
	 *
	 * Deployments are filtered by exact `strategy_fingerprint`; other versions
	 * stay listed separately. Pause / resume / stop / flatten go through the
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
		listAllDeployments,
		pauseDeployment,
		resumeDeployment,
		stopDeployment,
		type Deployment
	} from '$lib/deployments';
	import {
		livePreflight,
		paperEvidenceText,
		shortStrategyFingerprint,
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
	import PaperStartForm from '$lib/workspace/PaperStartForm.svelte';
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

	const selected = $derived(workspace.version.entry);
	const model = $derived(workspace.selectedModel);
	const fingerprint = $derived(selected?.strategy_fingerprint ?? '');
	const versionDeployments = $derived(
		deployments.filter(
			(deployment) => fingerprint !== '' && deployment.strategy_fingerprint === fingerprint
		)
	);
	const paper = $derived(versionDeployments.filter((deployment) => deployment.mode === 'paper'));
	const live = $derived(versionDeployments.filter((deployment) => deployment.mode === 'live'));
	const otherVersions = $derived(
		deployments.filter(
			(deployment) =>
				fingerprint !== '' &&
				deployment.strategy_fingerprint !== null &&
				deployment.strategy_fingerprint !== fingerprint
		)
	);
	const activePaper = $derived(paper.filter((deployment) => deployment.status !== 'stopped'));
	const activeLive = $derived(live.filter((deployment) => deployment.status !== 'stopped'));
	const controlsBlocked = $derived(mutating || outcomeUnknown);

	$effect(() => {
		const key = `${workspace.strategyId}:${fingerprint}`;
		void key;
		untrack(() => {
			// Switching version clears prior runtime rows before loading the new selection.
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

	async function loadDeployments(): Promise<boolean> {
		const requestId = ++loadRequest;
		const strategyId = workspace.strategyId;
		loading = true;
		loadError = null;
		try {
			const all = await listAllDeployments();
			if (requestId !== loadRequest) return false;
			deployments = all.filter((deployment) => deployment.strategy_id === strategyId);
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
				strategy_fingerprint: fingerprint,
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

	function onPaperStarted(deployment: Deployment): void {
		deployments = [...deployments, deployment];
		acceptedMessage = 'Paper deployment started.';
		void loadDeployments();
	}
</script>

<svelte:head><title>Run · {workspace.name ?? 'Strategy'} · ThyTrader</title></svelte:head>

{#if workspace.version.status === 'none'}
	<div class="empty-state">
		<h2>No published version yet</h2>
		<p>Drafts cannot run. Validate and publish this draft first.</p>
		<a class="btn" href={resolve(workspaceHref(workspace.strategyId, 'build'))}>Go to Build</a>
	</div>
{:else if selected && model}
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
							onaction={openLifecycle}
						/>
					{:else}
						<p class="muted">No paper deployment using this version.</p>
					{/each}
				{/if}
				{#if activePaper.length > 0}
					<details class="start-another">
						<summary>Start another paper deployment</summary>
						<PaperStartForm
							{fingerprint}
							version={selected.version}
							name={workspace.name ?? model.name}
							{model}
							disabled={controlsBlocked}
							onStarted={onPaperStarted}
						/>
					</details>
				{:else}
					<h3 class="sub">Start paper</h3>
					<PaperStartForm
						{fingerprint}
						version={selected.version}
						name={workspace.name ?? model.name}
						{model}
						disabled={controlsBlocked}
						onStarted={onPaperStarted}
					/>
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
					<DeploymentRuntimeRow {deployment} disabled={controlsBlocked} onaction={openLifecycle} />
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
					disabled={controlsBlocked || fingerprint === ''}
					onclick={openLive}>Arm live trading…</button
				>
			</div>
		</section>
	</div>

	{#if otherVersions.length > 0}
		<section class="card others" aria-labelledby="others-title">
			<div class="card-head"><h2 id="others-title">Other deployments for this strategy</h2></div>
			<div class="card-body">
				<p class="note">
					These run different published versions. Their evidence is not mixed into v{selected.version}.
				</p>
				<ul>
					{#each otherVersions as deployment (deployment.id)}
						<li>
							<a href={resolve(`/deployments/${encodeURIComponent(deployment.id)}`)}
								>{deployment.mode} · {deployment.status} · v{workspace.versionNumberOf(
									deployment.strategy_fingerprint
								) ?? '?'} · {shortStrategyFingerprint(deployment.strategy_fingerprint ?? '')}</a
							>
						</li>
					{/each}
				</ul>
			</div>
		</section>
	{/if}

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
			This starts a live deployment of <strong>{workspace.name ?? model.name}</strong>
			v{selected.version}
			that places real spot orders on Coinbase with your API keys. You are responsible for every trade
			and its market risk.
		</p>
		<div class="row">
			<span>Market</span><span>{marketLabel(model.product_id)} · {model.timeframe}</span>
		</div>
		<div class="row"><span>Coinbase product record</span><code>{model.product_id}</code></div>
		<div class="row"><span>Fingerprint</span><code class="fp">{fingerprint}</code></div>
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
{:else if selected && workspace.modelErrors[selected.strategy_fingerprint]}
	<div class="error-banner" role="alert">
		<div>
			<strong>Published definition unavailable</strong>
			<p>{workspace.modelErrors[selected.strategy_fingerprint]}</p>
		</div>
	</div>
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
	.others {
		margin-top: var(--space-4);
	}
	.others ul {
		margin: 0;
		padding-left: 18px;
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
