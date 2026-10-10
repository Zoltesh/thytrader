<script lang="ts">
	/**
	 * Bot detail (`/deployments/[id]`): one deployment running one rules
	 * snapshot of its strategy (ADR 0082). Header (mode chip, rules pill,
	 * lifecycle controls), an "earlier edit" notice with the guided update path
	 * when the strategy changed since start, four KPI cards (the latest bar names
	 * when the next evaluation is due), the per-bar decision timeline with each
	 * persisted trade reason joined onto its decision by intent, orders & fills,
	 * then progressively disclosed capital, configuration, and evidence. A kept
	 * live book of a deleted strategy says "(deleted strategy)".
	 *
	 * Lifecycle controls render only for a complete lifecycle contract and a
	 * fresh snapshot; an unknown outcome or stale refresh disables them until a
	 * reload succeeds. Live resume needs the explicit "real orders" checkbox
	 * before `i_understand_live: true` is sent. Live deployments turn on the
	 * shell's live chrome.
	 *
	 * A futures bot (a `-CDE` product) also shows its paper futures book in USD.
	 *
	 * This page owns every fetch and all mutation state; the panels under
	 * `$lib/deployment-detail/` only render it, except the futures book card, which
	 * reads its own endpoint for futures bots only.
	 */
	import { goto } from '$app/navigation';
	import { resolve } from '$app/paths';
	import { page as pageState } from '$app/state';
	import EarlierEditNotice from '$lib/workspace/EarlierEditNotice.svelte';
	import DecisionTimeline from '$lib/DecisionTimeline.svelte';
	import DeploymentTwin from '$lib/DeploymentTwin.svelte';
	import DeploymentLifecycleDialog from '$lib/DeploymentLifecycleDialog.svelte';
	import ActionBanners from '$lib/deployment-detail/ActionBanners.svelte';
	import BotHeader from '$lib/deployment-detail/BotHeader.svelte';
	import BotKpis from '$lib/deployment-detail/BotKpis.svelte';
	import CapitalDisclosure from '$lib/deployment-detail/CapitalDisclosure.svelte';
	import EvidenceCards from '$lib/deployment-detail/EvidenceCards.svelte';
	import FuturesBookCard from '$lib/deployment-detail/FuturesBookCard.svelte';
	import LedgerCard from '$lib/deployment-detail/LedgerCard.svelte';
	import PositionsCard from '$lib/deployment-detail/PositionsCard.svelte';
	import RulesDisclosure from '$lib/deployment-detail/RulesDisclosure.svelte';
	import { LedgerPager } from '$lib/deployment-detail/ledger-pager.svelte';
	import {
		acceptedAction,
		isAmbiguousTransportFailure,
		sendLifecycleAction
	} from '$lib/deployment-detail/lifecycle-actions';
	import { DECISION_RETENTION_NOTE } from '$lib/decisions';
	import {
		botTitle,
		exactVersionEvidenceLinks,
		lifecycleAcceptedMessage,
		otherVersionDeployments,
		quoteAmountLabel,
		strategyVersionLink,
		type LifecycleAction
	} from '$lib/deployment-detail';
	import { declareLiveContext } from '$lib/live-context.svelte';
	import { fetchTradeReasons } from '$lib/memory';
	import { rulesState } from '$lib/strategy-workspace';
	import {
		fetchStrategy,
		toBuilderModel,
		type BuilderModel,
		type StrategyRecord
	} from '$lib/strategies';
	import { sortTradeReasons, type TradeReasonState } from '$lib/trade-reasons';
	import {
		canonicalBooks,
		canonicalPositions,
		fetchDeployment,
		fetchDeploymentPerformance,
		listAllDeployments,
		listDeploymentFills,
		listDeploymentOrders,
		type Deployment,
		type DeploymentFill,
		type DeploymentOrder,
		type OperatorPerformanceReport
	} from '$lib/deployments';
	import { lifecycleControlsAvailable } from '$lib/lifecycle-contract';
	import { isFuturesProductId } from '$lib/product-id';
	import { loadStrategyConfig, type StrategySourceState } from '$lib/strategy-config';

	const id = $derived(pageState.params.id ?? '');

	let deployment = $state<Deployment | null>(null);
	/** Clock for each open book's held time (ADR 0098); a minute is fine-grained enough. */
	let now = $state(Date.now());
	$effect(() => {
		const clock = setInterval(() => (now = Date.now()), 60_000);
		return () => clearInterval(clock);
	});
	let inventory = $state<Deployment[]>([]);
	let loading = $state(true);
	let loadError = $state<string | null>(null);
	let stale = $state(false);
	let refreshError = $state<string | null>(null);

	// Exact-rules configuration summary from the snapshot API.
	let strategyConfig = $state<StrategySourceState | null>(null);
	// The owning strategy's current record (for "Current rules" / "Earlier edit").
	let strategyRecord = $state<StrategyRecord | null>(null);
	let strategyRecordFor = '';

	// Operator performance report: provenance-labeled numbers, not the raw ledger.
	let performanceReport = $state<OperatorPerformanceReport | null>(null);
	let performanceError = $state<string | null>(null);
	let performanceLoading = $state(false);
	let performanceRequestId = 0;

	// Cursor-paginated orders and fills with per-section error state.
	const ORDER_PAGE_SIZE = 50;
	const FILL_PAGE_SIZE = 50;
	const orders = new LedgerPager<DeploymentOrder>(
		(cursor) => listDeploymentOrders(id, ORDER_PAGE_SIZE, cursor),
		'The order history is unavailable.'
	);
	const fills = new LedgerPager<DeploymentFill>(
		(cursor) => listDeploymentFills(id, FILL_PAGE_SIZE, cursor),
		'The fill history is unavailable.'
	);

	// Dialog + mutation state. Fail closed: an unknown outcome disables actions
	// until a fresh snapshot is loaded successfully.
	let dialogAction = $state<LifecycleAction | null>(null);
	let stopWithFlatten = $state(false);
	let mutating = $state(false);
	let outcomeUnknown = $state(false);
	let acceptedMessage = $state<string | null>(null);
	let acceptedRevision: number | null = $state(null);
	let actionError = $state<string | null>(null);

	const current = $derived(deployment);
	const otherVersions = $derived(
		current === null ? [] : otherVersionDeployments(inventory, current)
	);
	/** Mutation controls stay disabled until a successful fresh refresh. */
	const controlsBlocked = $derived(outcomeUnknown || stale || current === null);
	const evidenceLinks = $derived(current === null ? [] : exactVersionEvidenceLinks(current));
	const positions = $derived(current === null ? [] : canonicalPositions(current));
	/** Run stage of this bot's strategy workspace, plus the snapshot it runs. */
	const versionLink = $derived(current === null ? null : strategyVersionLink(current));
	const currentFingerprint = $derived(strategyRecord?.current_fingerprint ?? null);
	const currentModel = $derived<BuilderModel | null>(
		strategyRecord?.strategy
			? toBuilderModel(strategyRecord.strategy, strategyRecord.revision)
			: null
	);
	const botRules = $derived(rulesState(current?.strategy_fingerprint, currentFingerprint));
	const loadedConfig = $derived(strategyConfig?.kind === 'loaded' ? strategyConfig : null);

	// Orders & fills share one card; the switch keeps each list's own paging.
	let ledgerView = $state<'orders' | 'fills'>('orders');

	// Persisted trade reasons for this deployment only; the decision timeline
	// joins each onto its decision row by intent.
	let reasons = $state<TradeReasonState | undefined>(undefined);
	/** A multi-instrument bot journals one decision per product per bar. */
	const multiProduct = $derived(current !== null && canonicalBooks(current).length > 1);
	let reasonsRequest = 0;

	async function loadReasons(): Promise<void> {
		const requestId = ++reasonsRequest;
		reasons = { status: 'loading' };
		try {
			const records = await fetchTradeReasons({ deploymentId: id });
			if (requestId !== reasonsRequest) return;
			reasons = { status: 'ready', records: sortTradeReasons(records) };
		} catch (caught) {
			if (requestId !== reasonsRequest) return;
			reasons = {
				status: 'error',
				message: caught instanceof Error ? caught.message : 'Why-trade records are unavailable.'
			};
		}
	}

	$effect(() => {
		// Live exposure on screen: the shell shows the amber strip and inset frame.
		if (current === null || current.mode !== 'live') return;
		const allocated = current.capital?.allocated_capital ?? null;
		return declareLiveContext({
			kind: 'bot',
			productId: current.product_id,
			cap: allocated === null ? null : quoteAmountLabel(allocated, current.product_id)
		});
	});

	async function loadDetail(): Promise<void> {
		loading = true;
		loadError = null;
		try {
			const fresh = await fetchDeployment(id);
			deployment = fresh;
			stale = false;
			refreshError = null;
			outcomeUnknown = false;
			actionError = null;
		} catch (caught) {
			loadError = caught instanceof Error ? caught.message : 'Could not load this deployment.';
		} finally {
			loading = false;
		}
	}

	/** Background inventory load for the other-versions section; non-fatal. */
	async function loadInventory(): Promise<void> {
		try {
			// Follow every page: other-version discovery must not silently stop at
			// the first 200 rows of a large inventory.
			inventory = await listAllDeployments();
		} catch {
			/* the detail stays usable; the section renders its own unavailable note */
		}
	}

	/** The owning strategy's current record; failures leave the rules state unknown. */
	async function loadStrategyRecord(strategyId: string): Promise<void> {
		strategyRecordFor = strategyId;
		try {
			const record = await fetchStrategy(strategyId);
			if (strategyRecordFor === strategyId) strategyRecord = record;
		} catch {
			if (strategyRecordFor === strategyId) strategyRecord = null;
		}
	}

	async function onBotUpdated(result: {
		stopped: Deployment;
		started: Deployment | null;
	}): Promise<void> {
		if (result.started !== null) {
			await goto(resolve(`/deployments/${encodeURIComponent(result.started.id)}`));
			return;
		}
		deployment = result.stopped;
		await refreshAfterMutation();
	}

	/** Exact-rules config summary; explicit unavailable state on failure. */
	async function loadStrategyConfigFor(fingerprint: string): Promise<void> {
		strategyConfig = await loadStrategyConfig(fingerprint);
	}

	async function loadPerformance(): Promise<void> {
		const requestId = performanceRequestId + 1;
		performanceRequestId = requestId;
		performanceLoading = true;
		performanceError = null;
		try {
			const report = await fetchDeploymentPerformance(id);
			if (requestId !== performanceRequestId) return;
			performanceReport = report;
		} catch (caught) {
			if (requestId !== performanceRequestId) return;
			performanceReport = null;
			performanceError =
				caught instanceof Error
					? caught.message
					: 'The operator performance report is unavailable.';
		} finally {
			if (requestId === performanceRequestId) performanceLoading = false;
		}
	}

	/**
	 * Refresh after a successful mutation.
	 *
	 * Failure keeps the mutation response merged (acceptedMessage/revision) and
	 * renders a partial-success state instead of discarding the acceptance.
	 * `controlsBlocked` stays true until this succeeds.
	 */
	async function refreshAfterMutation(): Promise<void> {
		try {
			const fresh = await fetchDeployment(id);
			deployment = fresh;
			stale = false;
			refreshError = null;
			outcomeUnknown = false;
		} catch {
			stale = true;
			refreshError =
				'The action was accepted, but the latest snapshot could not be loaded. Retry refresh before taking another action.';
		}
	}

	function openDialog(action: LifecycleAction): void {
		dialogAction = action;
		stopWithFlatten = false;
	}

	function closeDialog(): void {
		if (mutating) return;
		dialogAction = null;
	}

	async function confirmDialog(options: { liveAcknowledged: boolean }): Promise<void> {
		const action = dialogAction;
		if (action === null || current === null || mutating) return;
		mutating = true;
		actionError = null;
		acceptedMessage = null;
		acceptedRevision = null;
		const targetId = id;
		try {
			const updated = await sendLifecycleAction(action, targetId, {
				liveAcknowledged: current.mode === 'live' && options.liveAcknowledged,
				stopWithFlatten
			});
			// Merge the mutation response before the reconciliation refresh.
			deployment = updated;
			acceptedRevision = updated.revision;
			acceptedMessage = lifecycleAcceptedMessage(acceptedAction(action, stopWithFlatten));
			dialogAction = null;
			await refreshAfterMutation();
			if (!stale) {
				void loadPerformance();
				void orders.load(undefined, 0);
				void fills.load(undefined, 0);
				void loadReasons();
			}
		} catch (caught) {
			const message = caught instanceof Error ? caught.message : 'The action could not be sent.';
			if (isAmbiguousTransportFailure(message)) {
				// Ambiguous network interruption: do not retry automatically. Close the
				// dialog so the outcome-unknown banner and refresh path are reachable.
				outcomeUnknown = true;
				dialogAction = null;
				actionError =
					'Outcome unknown. The request may or may not have been applied. Refresh this deployment before retrying the action.';
			} else {
				actionError = message;
			}
		} finally {
			mutating = false;
		}
	}

	$effect(() => {
		if (id === '') return;
		void loadDetail();
		void loadInventory();
		void loadPerformance();
		void orders.load(undefined, 0);
		void fills.load(undefined, 0);
		void loadReasons();
	});

	$effect(() => {
		const strategyId = current?.strategy_id ?? null;
		if (strategyId === null) {
			strategyRecord = null;
			strategyRecordFor = '';
			return;
		}
		if (strategyRecordFor === strategyId) return;
		void loadStrategyRecord(strategyId);
	});

	$effect(() => {
		const fingerprint = current?.strategy_fingerprint ?? null;
		if (fingerprint === null) {
			strategyConfig = null;
			return;
		}
		if (strategyConfig !== null && strategyConfig.fingerprint === fingerprint) return;
		void loadStrategyConfigFor(fingerprint);
	});
</script>

<svelte:head
	><title>{current ? botTitle(current, loadedConfig?.name ?? null) : 'Bot'} · ThyTrader</title
	></svelte:head
>

<main>
	<a class="back-link" href={resolve('/deployments')}>← Portfolio</a>
	{#if loading}
		<section class="loading-card" aria-label="Loading deployment">
			<div class="skeleton wide"></div>
			<div class="skeleton"></div>
		</section>
	{:else if loadError}
		<div class="error-banner" role="alert">
			<div>
				<strong>Couldn't load this deployment</strong>
				<p>{loadError}</p>
			</div>
			<button type="button" onclick={() => void loadDetail()}>Try again</button>
		</div>
	{:else if current}
		{@const controlsAvailable = lifecycleControlsAvailable(current) && !controlsBlocked}
		<BotHeader
			{current}
			strategyName={loadedConfig?.name ?? null}
			{versionLink}
			{botRules}
			{controlsAvailable}
			{mutating}
			onaction={openDialog}
		/>

		{#if current.portfolio_id}
			<p class="contract-note" data-testid="portfolio-sleeve-note">
				This bot is a sleeve of a portfolio: its entries also pass the portfolio's exposure caps and
				loss stops, and the portfolio's weights set its capital.
				<a href={resolve(`/deployments?portfolio=${encodeURIComponent(current.portfolio_id)}`)}
					>Open the portfolio</a
				>
			</p>
		{/if}
		{#if current.strategy_deleted}
			<p class="contract-note" data-testid="deleted-strategy-note" role="status">
				Its strategy was deleted. This live bot's orders, fills, positions, trade reasons, and the
				rules it ran are kept.
			</p>
		{/if}
		<DeploymentTwin deployment={current} {inventory} disabled={stale || mutating} />

		<EarlierEditNotice
			deployment={current}
			{currentFingerprint}
			current={currentModel}
			disabled={controlsBlocked || mutating || !lifecycleControlsAvailable(current)}
			onupdated={(result) => void onBotUpdated(result)}
		/>

		<ActionBanners
			{current}
			{controlsAvailable}
			{controlsBlocked}
			{stale}
			{refreshError}
			{acceptedRevision}
			{acceptedMessage}
			{outcomeUnknown}
			{actionError}
			dialogOpen={dialogAction !== null}
			{mutating}
			onretryrefresh={() => void refreshAfterMutation()}
			onrefresh={() => void loadDetail()}
			onaction={openDialog}
		/>

		<BotKpis
			{current}
			{positions}
			{now}
			{performanceReport}
			{performanceError}
			{performanceLoading}
		/>

		{#if isFuturesProductId(current.product_id)}
			<FuturesBookCard deploymentId={current.id} revision={current.revision} />
		{/if}

		<section class="card why" aria-labelledby="why-title" data-testid="why-it-traded">
			<div class="card-head"><h2 id="why-title">Decisions</h2></div>
			<DecisionTimeline
				source={{ kind: 'deployment', deploymentId: current.id }}
				{reasons}
				{multiProduct}
				timeframe={current.timeframe}
				discretionary={current.kind === 'discretionary'}
			/>
			<p class="history-note" data-testid="decision-retention-note">{DECISION_RETENTION_NOTE}</p>
		</section>

		<LedgerCard productId={current.product_id} {orders} {fills} bind:view={ledgerView} />

		{#if positions.length > 0}
			<PositionsCard {positions} {now} />
		{/if}

		<EvidenceCards deploymentId={id} {evidenceLinks} {otherVersions} />

		<CapitalDisclosure {current} />

		<RulesDisclosure {current} {strategyConfig} />
	{/if}
</main>

<DeploymentLifecycleDialog
	deployment={dialogAction === null ? null : current}
	action={dialogAction}
	bind:stopWithFlatten
	{mutating}
	{actionError}
	requireLiveAcknowledgement
	oncancel={closeDialog}
	onconfirm={(options) => void confirmDialog(options)}
/>

<style>
	.back-link {
		display: inline-block;
		margin-bottom: 14px;
		color: var(--muted);
		text-decoration: none;
	}
	.back-link:hover {
		color: var(--text);
		text-decoration: underline;
	}
	.card {
		margin-bottom: 16px;
	}
	.card-head {
		display: flex;
		align-items: center;
		gap: 10px;
		padding: 12px 16px;
		border-bottom: 1px solid var(--line);
	}
	.card-head h2 {
		margin-right: auto;
	}
	.history-note {
		margin: 0;
		padding: 10px 16px;
		border-top: 1px dashed var(--line-2);
		color: var(--faint);
		font-size: var(--fs-sm);
	}
	.contract-note {
		margin: 0 0 12px;
		color: var(--warn);
		font-size: var(--fs-sm);
	}
</style>
