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
	 */
	import { goto } from '$app/navigation';
	import { resolve } from '$app/paths';
	import { page as pageState } from '$app/state';
	import EarlierEditNotice from '$lib/workspace/EarlierEditNotice.svelte';
	import DecisionTimeline from '$lib/DecisionTimeline.svelte';
	import DeploymentTwin from '$lib/DeploymentTwin.svelte';
	import DeploymentLifecycleDialog from '$lib/DeploymentLifecycleDialog.svelte';
	import Segmented from '$lib/Segmented.svelte';
	import { DECISION_RETENTION_NOTE, nextEvaluationText } from '$lib/decisions';
	import {
		botTitle,
		canOfferFlatten,
		capitalBreakdown,
		drawdownIsCaveated,
		EVIDENCE_BOUNDED_NOTE,
		eligibility,
		exactVersionEvidenceLinks,
		fingerprintText,
		lastEvaluatedText,
		latestBarHeadline,
		leaseText,
		ledgerPerformanceText,
		lifecycleAcceptedMessage,
		marketLabel,
		otherVersionDeployments,
		performanceHeadline,
		productIdQuote,
		performanceReportText,
		performanceCurrencySuffix,
		quoteAmountLabel,
		strategyVersionLink,
		type LifecycleAction
	} from '$lib/deployment-detail';
	import { pnlOf, positionText, protectionText } from '$lib/deployment-portfolio';
	import { declareLiveContext } from '$lib/live-context.svelte';
	import { fetchTradeReasons } from '$lib/memory';
	import { rulesLabel, rulesState, shortStrategyFingerprint } from '$lib/strategy-workspace';
	import {
		fetchStrategy,
		toBuilderModel,
		type BuilderModel,
		type StrategyRecord
	} from '$lib/strategies';
	import { formatUtcTimestamp } from '$lib/time';
	import { sortTradeReasons, type TradeReasonState } from '$lib/trade-reasons';
	import {
		canonicalBooks,
		canonicalPositions,
		fetchDeployment,
		fetchDeploymentPerformance,
		listAllDeployments,
		listDeploymentFills,
		listDeploymentOrders,
		pauseDeployment,
		resetBreakerLatches,
		resumeDeployment,
		stopDeployment,
		type Deployment,
		type DeploymentFill,
		type DeploymentOrder,
		type OperatorPerformanceReport
	} from '$lib/deployments';
	import { lifecycleControlsAvailable } from '$lib/lifecycle-contract';
	import { heldText, markTitle, unrealizedText } from '$lib/open-books';
	import { protectionBadge } from '$lib/protection-evidence';
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
	let orderPage = $state<{ rows: DeploymentOrder[]; nextCursor: string | null }>({
		rows: [],
		nextCursor: null
	});
	let orderError = $state<string | null>(null);
	let orderLoading = $state(false);
	let orderCursors = $state<(string | undefined)[]>([undefined]);
	let orderPageIndex = $state(0);
	let fillPage = $state<{ rows: DeploymentFill[]; nextCursor: string | null }>({
		rows: [],
		nextCursor: null
	});
	let fillError = $state<string | null>(null);
	let fillLoading = $state(false);
	let fillCursors = $state<(string | undefined)[]>([undefined]);
	let fillPageIndex = $state(0);

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
	const performancePayload = $derived(performanceReport?.payload ?? null);
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
	const capitalRows = $derived(current === null ? null : capitalBreakdown(current));

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

	const ORDER_PAGE_SIZE = 50;
	const FILL_PAGE_SIZE = 50;

	async function loadOrdersPage(cursor?: string, pageIndex = 0): Promise<void> {
		orderLoading = true;
		orderError = null;
		try {
			const page = await listDeploymentOrders(id, ORDER_PAGE_SIZE, cursor);
			orderPage = page;
			orderPageIndex = pageIndex;
			orderCursors = [...orderCursors.slice(0, pageIndex), cursor];
		} catch (caught) {
			orderError = caught instanceof Error ? caught.message : 'The order history is unavailable.';
		} finally {
			orderLoading = false;
		}
	}

	async function loadFillsPage(cursor?: string, pageIndex = 0): Promise<void> {
		fillLoading = true;
		fillError = null;
		try {
			const page = await listDeploymentFills(id, FILL_PAGE_SIZE, cursor);
			fillPage = page;
			fillPageIndex = pageIndex;
			fillCursors = [...fillCursors.slice(0, pageIndex), cursor];
		} catch (caught) {
			fillError = caught instanceof Error ? caught.message : 'The fill history is unavailable.';
		} finally {
			fillLoading = false;
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
			let updated: Deployment;
			if (action === 'pause') updated = await pauseDeployment(targetId);
			else if (action === 'resume')
				// Live resume sends i_understand_live only after the dialog's ticked checkbox.
				updated = await resumeDeployment(targetId, {
					liveAcknowledged: current.mode === 'live' && options.liveAcknowledged
				});
			else if (action === 'flatten') updated = await stopDeployment(targetId, true);
			else if (action === 'reset-breakers') updated = await resetBreakerLatches(targetId);
			else updated = await stopDeployment(targetId, stopWithFlatten);
			// Merge the mutation response before the reconciliation refresh.
			deployment = updated;
			acceptedRevision = updated.revision;
			acceptedMessage = lifecycleAcceptedMessage(
				stopWithFlatten && action === 'stop' ? 'flatten' : action
			);
			dialogAction = null;
			await refreshAfterMutation();
			if (!stale) {
				void loadPerformance();
				void loadOrdersPage(undefined, 0);
				void loadFillsPage(undefined, 0);
				void loadReasons();
			}
		} catch (caught) {
			const message = caught instanceof Error ? caught.message : 'The action could not be sent.';
			if (message === 'Failed to fetch' || message.includes('NetworkError')) {
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
		void loadOrdersPage(undefined, 0);
		void loadFillsPage(undefined, 0);
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
		{@const identity = eligibility(current)}
		{@const controlsAvailable = lifecycleControlsAvailable(current) && !controlsBlocked}
		{@const isLive = current.mode === 'live'}
		<header class="bot-head">
			<div class="bot-id">
				<div class="title-row">
					<h1>{botTitle(current, loadedConfig?.name ?? null)}</h1>
					<span class="chip" class:paper={!isLive} class:live={isLive} data-testid="mode-chip"
						>{isLive ? 'LIVE' : 'Paper'}</span
					>
					{#if versionLink}
						<a
							class="pill"
							href={resolve(versionLink.href)}
							data-testid="version-pill"
							data-rules={botRules}
							title="Rules snapshot {versionLink.fingerprint}. Open the strategy workspace."
							>{botRules === 'unknown'
								? shortStrategyFingerprint(versionLink.fingerprint)
								: rulesLabel(botRules)} →</a
						>
					{/if}
				</div>
				<p class="lede" data-testid="bot-lede">
					{marketLabel(current.product_id)} · {current.timeframe ?? 'clock unknown'} ·
					<span data-testid="deployment-status">{identity.status}</span> ·
					<span title="A held lease is coordination state, not proof that the worker is healthy"
						>{leaseText(current)}</span
					>
				</p>
				<p class="facts">
					<span>Instruction <b>{identity.instruction}</b></span>
					<span>Entries <b data-testid="deployment-eligibility">{identity.eligibility}</b></span>
					<span>Revision <b>{current.revision}</b></span>
				</p>
			</div>
			<div class="controls">
				{#if controlsAvailable}
					{#if current.status === 'running'}
						<button
							type="button"
							class="btn"
							disabled={mutating}
							onclick={() => openDialog('pause')}>Pause entries…</button
						>
					{/if}
					{#if current.status === 'paused'}
						<button
							type="button"
							class="btn"
							class:live={isLive}
							disabled={mutating}
							onclick={() => openDialog('resume')}>Resume entries…</button
						>
					{/if}
					{#if current.status !== 'stopped'}
						<button
							type="button"
							class="btn danger"
							disabled={mutating}
							onclick={() => openDialog('stop')}>Stop…</button
						>
					{/if}
					{#if canOfferFlatten(current)}
						<button
							type="button"
							class="btn danger"
							disabled={mutating}
							onclick={() => openDialog('flatten')}>Flatten remaining exposure…</button
						>
					{/if}
				{/if}
			</div>
		</header>

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

		{#if !controlsAvailable}
			<p class="contract-note" data-testid="controls-blocked-note" role="status">
				{controlsBlocked
					? 'Lifecycle controls are disabled until this deployment is refreshed successfully. An earlier action had an unknown outcome or the snapshot is stale; refresh to re-enable.'
					: 'Lifecycle controls are unavailable because this server did not return the current lifecycle contract. Values are shown read-only; nothing is inferred.'}
			</p>
		{/if}

		{#if stale}
			<div class="warn-banner" role="status" data-testid="stale-banner">
				<div>
					<strong>Stale snapshot</strong>
					<p>{refreshError}</p>
					{#if acceptedRevision !== null}
						<p>Showing response revision {acceptedRevision}.</p>
					{/if}
				</div>
				<button type="button" onclick={() => void refreshAfterMutation()}>Retry refresh</button>
			</div>
		{/if}
		{#if outcomeUnknown}
			<div class="warn-banner" role="alert" data-testid="outcome-unknown-banner">
				<div>
					<strong>Outcome unknown</strong>
					<p>{actionError}</p>
				</div>
				<!-- Refresh first; controls stay disabled until this load succeeds. -->
				<button
					type="button"
					data-testid="outcome-unknown-refresh"
					onclick={() => {
						void loadDetail();
					}}>Refresh now</button
				>
			</div>
		{:else if actionError && dialogAction === null}
			<div class="error-banner" role="alert">
				<div>
					<strong>Action failed</strong>
					<p>{actionError}</p>
				</div>
			</div>
		{/if}
		{#if acceptedMessage && !stale}
			<div class="ok-banner" role="status">
				<p>{acceptedMessage}</p>
			</div>
		{/if}
		{#if current.mismatch_detail}
			<p class="problem-banner" role="alert">{current.mismatch_detail}</p>
		{/if}
		{#if current.daily_loss_latched || current.drawdown_latched}
			<div class="breaker-row" data-testid="breaker-row">
				<p class="problem" role="status">
					{[
						current.daily_loss_latched ? 'Daily-loss breaker latched' : null,
						current.drawdown_latched ? 'Drawdown breaker latched' : null
					]
						.filter(Boolean)
						.join(' · ')} — new entries are blocked until you reset it.
				</p>
				<!-- Confirmation required: the reset dialog names the consequence
				     before any POST is sent. Hidden while controls are unavailable. -->
				{#if controlsAvailable}
					<button
						type="button"
						class="btn"
						disabled={mutating}
						data-testid="reset-breakers-button"
						onclick={() => openDialog('reset-breakers')}>Reset breaker latches…</button
					>
				{/if}
			</div>
		{/if}

		<section class="kpis" aria-label="Key figures">
			<article class="card kpi" data-testid="kpi-capital">
				<h2 class="label">{isLive ? 'Allocated capital' : 'Allocated capital (paper)'}</h2>
				{#if current.capital && (current.capital.allocated_capital || current.capital.performance_equity)}
					<p class="value">
						{quoteAmountLabel(current.capital.allocated_capital, current.product_id)}
					</p>
					<p class="delta">
						Performance equity {quoteAmountLabel(
							current.capital.performance_equity,
							current.product_id
						)}{isLive
							? ` · venue ${quoteAmountLabel(current.capital.venue_available_quote, current.product_id)}`
							: ' · simulated'}
					</p>
				{:else}
					<p class="value">—</p>
					<p class="delta">No capital accounting on this snapshot.</p>
				{/if}
			</article>
			<article class="card kpi" data-testid="kpi-pnl">
				<h2 class="label">PnL</h2>
				{#if performanceLoading && performanceReport === null}
					<p class="value">—</p>
					<p class="delta" data-testid="performance-loading">Loading operator performance…</p>
				{:else if performanceError !== null}
					{@const fallback = pnlOf(current)}
					<p class="value" class:pos={fallback.tone === 'pos'} class:neg={fallback.tone === 'neg'}>
						{fallback.text}
					</p>
					<p class="delta problem" data-testid="performance-error" role="status">
						Operator performance report unavailable ({performanceError}). Ledger summary:
						{ledgerPerformanceText(current)}
					</p>
				{:else if performancePayload}
					{@const headline = performanceHeadline(performancePayload)}
					<p
						class="value"
						class:pos={headline.startsWith('+')}
						class:neg={headline.startsWith('-')}
					>
						{headline}
					</p>
					<p class="delta" data-testid="deployment-performance">
						{performanceReportText(performancePayload)}
					</p>
					<p class="delta">
						Fill ledger{performanceCurrencySuffix(performancePayload.currency).trim() === ''
							? ' · quote currency unknown'
							: ''} · {isLive ? 'after Coinbase fees' : 'assumed paper fees'}
					</p>
					{#if drawdownIsCaveated(performancePayload)}
						<p class="delta" data-testid="performance-drawdown-caveat">
							Drawdown {(Number(performancePayload.maximum_drawdown_fraction) * 100).toFixed(2)}%
							from fill-event marks, not a bar equity curve; it understates intra-bar drawdown.
						</p>
					{/if}
					{#each performanceReport?.partial_result_warnings ?? [] as warning (warning)}
						<p class="delta contract-note" role="status">{warning}</p>
					{/each}
				{:else}
					<p class="value">—</p>
					<p class="delta">{ledgerPerformanceText(current)}</p>
				{/if}
			</article>
			<article class="card kpi" data-testid="kpi-position">
				<h2 class="label">Position</h2>
				<p class="value small">{positionText(positions)}</p>
				<p class="delta">{protectionText(current, positions)}</p>
				{#if positions.length === 1}
					{@const book = positions[0]!}
					{@const pnl = unrealizedText(book, productIdQuote(book.product_id) ?? '')}
					{@const held = `held ${heldText(book.entered_bar, now)}`}
					<p class="delta" data-testid="kpi-position-pnl">
						{#if pnl !== null}<span class="upnl {pnl.tone}" title={markTitle(book)}
								>uPnL {pnl.text}</span
							>{` · ${held}`}{:else}{held}{/if}
					</p>
				{/if}
			</article>
			<article class="card kpi" data-testid="kpi-latest-bar">
				<h2 class="label">Latest bar</h2>
				<p class="value small">{latestBarHeadline(current)}</p>
				<p class="delta">{lastEvaluatedText(current)}</p>
				{#if current.kind !== 'discretionary'}
					<p class="delta" data-testid="next-evaluation">{nextEvaluationText(current)}</p>
				{/if}
			</article>
		</section>

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

		<section class="card ledger" aria-labelledby="ledger-title">
			<div class="card-head">
				<h2 id="ledger-title">Orders &amp; fills</h2>
				<Segmented
					label="Show orders or fills"
					options={[
						{ id: 'orders', label: 'Orders' },
						{ id: 'fills', label: 'Fills' }
					]}
					value={ledgerView}
					onchange={(next) => (ledgerView = next)}
					testId="ledger-switch"
				/>
			</div>
			{#if ledgerView === 'orders'}
				{#if orderError}
					<p class="pad problem" role="status" data-testid="orders-error">
						Order history could not be loaded ({orderError})
						<button
							type="button"
							class="btn"
							onclick={() => void loadOrdersPage(orderCursors[orderPageIndex], orderPageIndex)}
						>
							Retry
						</button>
					</p>
				{:else if orderLoading && orderPage.rows.length === 0}
					<p class="pad quiet" data-testid="orders-loading">Loading orders…</p>
				{:else if orderPage.rows.length === 0}
					<p class="pad quiet" data-testid="orders-empty">
						No orders recorded for this deployment.
					</p>
				{:else}
					<!-- svelte-ignore a11y_no_noninteractive_tabindex -->
					<div class="table-scroll" tabindex="0" role="region" aria-label="Order history">
						<table>
							<caption class="sr-only">Order history</caption>
							<thead>
								<tr>
									<th scope="col">Time (UTC)</th>
									<th scope="col">Product</th>
									<th scope="col">Side</th>
									<th scope="col">Type</th>
									<th scope="col" class="num">Size</th>
									<th scope="col" class="num">Price</th>
									<th scope="col">Status</th>
								</tr>
							</thead>
							<tbody>
								{#each orderPage.rows as order (order.id)}
									<tr>
										<td class="mono muted">{formatUtcTimestamp(order.created_at).slice(5, 16)}</td>
										<td>{order.product_id || current.product_id}</td>
										<td>{order.side}</td>
										<td class="muted">{order.kind}</td>
										<td class="num">{order.quantity}</td>
										<td class="num">{order.price ?? '—'}</td>
										<td class="muted"
											>{order.status}{order.reject_reason ? ` · ${order.reject_reason}` : ''}</td
										>
									</tr>
								{/each}
							</tbody>
						</table>
					</div>
					<div class="pager" data-testid="orders-pager">
						<span>
							Showing {orderPage.rows.length} order{orderPage.rows.length === 1 ? '' : 's'}
							{orderPage.nextCursor !== null ? ' · more available' : ''}
						</span>
						<button
							type="button"
							disabled={orderLoading || orderPageIndex === 0}
							onclick={() =>
								void loadOrdersPage(orderCursors[orderPageIndex - 1], orderPageIndex - 1)}
							>Previous</button
						>
						<button
							type="button"
							disabled={orderLoading || orderPage.nextCursor === null}
							onclick={() =>
								void loadOrdersPage(orderPage.nextCursor ?? undefined, orderPageIndex + 1)}
						>
							Next
						</button>
					</div>
				{/if}
			{:else if fillError}
				<p class="pad problem" role="status" data-testid="fills-error">
					Fill history could not be loaded ({fillError})
					<button
						type="button"
						class="btn"
						onclick={() => void loadFillsPage(fillCursors[fillPageIndex], fillPageIndex)}
					>
						Retry
					</button>
				</p>
			{:else if fillLoading && fillPage.rows.length === 0}
				<p class="pad quiet" data-testid="fills-loading">Loading fills…</p>
			{:else if fillPage.rows.length === 0}
				<p class="pad quiet" data-testid="fills-empty">No fills recorded for this deployment.</p>
			{:else}
				<!-- svelte-ignore a11y_no_noninteractive_tabindex -->
				<div class="table-scroll" tabindex="0" role="region" aria-label="Fill history">
					<table>
						<caption class="sr-only">Fill history</caption>
						<thead>
							<tr>
								<th scope="col">Time (UTC)</th>
								<th scope="col">Product</th>
								<th scope="col" class="num">Size</th>
								<th scope="col" class="num">Price</th>
								<th scope="col" class="num">Fee</th>
							</tr>
						</thead>
						<tbody>
							{#each fillPage.rows as fill (fill.id)}
								<tr>
									<td class="mono muted">{formatUtcTimestamp(fill.filled_at).slice(5, 16)}</td>
									<td>{fill.product_id || current.product_id}</td>
									<td class="num">{fill.quantity}</td>
									<td class="num">{fill.price}</td>
									<td class="num">{fill.fee}</td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
				<div class="pager" data-testid="fills-pager">
					<span>
						Showing {fillPage.rows.length} fill{fillPage.rows.length === 1 ? '' : 's'}
						{fillPage.nextCursor !== null ? ' · more available' : ''}
					</span>
					<button
						type="button"
						disabled={fillLoading || fillPageIndex === 0}
						onclick={() => void loadFillsPage(fillCursors[fillPageIndex - 1], fillPageIndex - 1)}
						>Previous</button
					>
					<button
						type="button"
						disabled={fillLoading || fillPage.nextCursor === null}
						onclick={() => void loadFillsPage(fillPage.nextCursor ?? undefined, fillPageIndex + 1)}
					>
						Next
					</button>
				</div>
			{/if}
		</section>

		{#if positions.length > 0}
			<section class="card" aria-labelledby="positions-title">
				<div class="card-head"><h2 id="positions-title">Positions &amp; protection</h2></div>
				<!-- svelte-ignore a11y_no_noninteractive_tabindex -->
				<div class="table-scroll" tabindex="0" role="region" aria-label="Open positions">
					<table>
						<caption class="sr-only">Open positions with protection status</caption>
						<thead>
							<tr>
								<th scope="col">Product</th>
								<th scope="col">Side</th>
								<th scope="col" class="num">Quantity</th>
								<th scope="col" class="num">Entry</th>
								<th scope="col" class="num">Stop</th>
								<th scope="col" class="num">Target</th>
								<th scope="col" class="num">Unrealized</th>
								<th scope="col" class="num">Held</th>
								<th scope="col">Protection</th>
							</tr>
						</thead>
						<tbody>
							{#each positions as position (position.product_id)}
								{@const pnl = unrealizedText(position, productIdQuote(position.product_id) ?? '')}
								{@const badge = protectionBadge(position, { fallback: 'sentence' })}
								<tr>
									<td>{position.product_id}</td>
									<td>{position.side ?? 'long'}</td>
									<td class="num">{position.quantity}</td>
									<td class="num">{position.entry_price}</td>
									<td class="num">{position.stop_price}</td>
									<td class="num">{position.target_price ?? 'none'}</td>
									<td
										class="num upnl {pnl?.tone ?? 'muted'}"
										title={markTitle(position)}
										data-testid="position-upnl">{pnl?.text ?? '—'}</td
									>
									<td
										class="num muted"
										title="Entry bar {position.entered_bar.slice(0, 16)} UTC"
										data-testid="position-held">{heldText(position.entered_bar, now)}</td
									>
									<td
										data-testid="position-state"
										class="state-cell {badge.tone}"
										title={badge.title}
										>{badge.text}{#if position.protection}<span
												class="protection-evidence"
												data-testid="protection-evidence">{badge.detail}</span
											>{/if}</td
									>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
			</section>
		{/if}

		<div class="grid2 even">
			<section class="card" aria-labelledby="evidence-title">
				<div class="card-head"><h2 id="evidence-title">Evidence for this strategy</h2></div>
				<div class="pad">
					<p class="evidence-links">
						{#if evidenceLinks.length === 0}
							<span class="quiet">No strategy evidence for a discretionary deployment.</span>
						{:else}
							{#each evidenceLinks as link (link.href)}
								<!-- eslint-disable-next-line svelte/no-navigation-without-resolve -- dynamic cross-route link with query -->
								<a data-testid="evidence-link" href={link.href}>{link.label}</a>
							{/each}
						{/if}
						<a href={resolve('/journals')}>Trade journals</a>
						<a href={resolve('/audit')}>Audit log</a>
						<a
							data-testid="execution-quality-link"
							href={resolve(`/deployments/${id}/execution-quality`)}
						>
							Execution quality
						</a>
					</p>
					<p class="quiet small">{EVIDENCE_BOUNDED_NOTE}</p>
				</div>
			</section>
			<section class="card" aria-label="Other deployments for this strategy">
				<div class="card-head"><h2>Other deployments for this strategy</h2></div>
				<div class="pad">
					{#if otherVersions.length === 0}
						<p class="quiet">None on this workstation.</p>
					{:else}
						<ul class="other-list" role="list">
							{#each otherVersions as other (other.id)}
								<li>
									<a href={resolve(`/deployments/${encodeURIComponent(other.id)}`)}>
										{other.mode} · {other.status} · {marketLabel(other.product_id)} · fingerprint
										{other.strategy_fingerprint?.slice(0, 18) ?? 'unknown'}…
									</a>
								</li>
							{/each}
						</ul>
						<p class="quiet small">
							Different rules snapshots; their evidence is not mixed into this page.
						</p>
					{/if}
				</div>
			</section>
		</div>

		<details class="card disclosure" data-testid="capital-disclosure">
			<summary>Capital breakdown</summary>
			<div class="pad">
				{#if capitalRows === null}
					<p class="quiet">No capital accounting on this snapshot.</p>
				{:else}
					<dl class="kv">
						{#each capitalRows as item (item.label)}
							<div>
								<dt>{item.label}</dt>
								<dd>{item.value}</dd>
							</div>
						{/each}
					</dl>
				{/if}
				<dl class="kv">
					<div>
						<dt>Ledger cash</dt>
						<dd>{quoteAmountLabel(current.cash, current.product_id)}</dd>
					</div>
					{#if !isLive}
						<div>
							<dt>Paper starting cash</dt>
							<dd>{quoteAmountLabel(current.paper_starting_cash, current.product_id)}</dd>
						</div>
						<div>
							<dt>Assumed maker / taker fee</dt>
							<dd>{current.maker_fee_rate ?? 'default'} / {current.taker_fee_rate ?? 'default'}</dd>
						</div>
					{/if}
				</dl>
				<p class="quiet small">
					Live books size from allocated capital or venue available quote, not ledger cash.
				</p>
			</div>
		</details>

		<details class="card disclosure" data-testid="config-disclosure">
			<summary>
				Exact rules this bot runs
				<code data-testid="deployment-fingerprint">{fingerprintText(current)}</code>
			</summary>
			<div class="pad">
				{#if current.strategy_fingerprint === null}
					<p class="quiet">Discretionary deployments have no strategy rules snapshot.</p>
				{:else if strategyConfig === null}
					<p class="quiet" data-testid="strategy-config-loading">Loading configuration…</p>
				{:else if strategyConfig.kind === 'unavailable'}
					<p class="contract-note" data-testid="strategy-config-unavailable" role="status">
						Rules snapshot unavailable ({strategyConfig.reason}). The fingerprint above is the only
						identity shown; the strategy's current edit was not substituted.
					</p>
				{:else}
					<div class="config-summary" data-testid="strategy-config-summary">
						<p class="config-rule">{strategyConfig.summary.rule}</p>
						<dl class="kv">
							{#each strategyConfig.summary.identity as fact (fact.label)}
								<div>
									<dt>{fact.label}</dt>
									<dd>{fact.value}</dd>
								</div>
							{/each}
						</dl>
						<p class="config-section">Indicators</p>
						<ul class="config-list">
							{#each strategyConfig.summary.indicators as line (line)}
								<li>{line}</li>
							{/each}
						</ul>
						<p class="config-section">Entry</p>
						<p>{strategyConfig.summary.entry}</p>
						{#if strategyConfig.summary.htfEntry !== null}
							<p class="config-section">HTF filter</p>
							<p>{strategyConfig.summary.htfEntry}</p>
						{/if}
						<p class="config-section">Exits</p>
						<ul class="config-list">
							{#each strategyConfig.summary.exits as line (line)}
								<li>{line}</li>
							{/each}
						</ul>
						<p class="config-section">Sizing &amp; limits</p>
						<ul class="config-list">
							{#each strategyConfig.summary.sizing as line (line)}
								<li>{line}</li>
							{/each}
						</ul>
					</div>
				{/if}
			</div>
		</details>
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
	.upnl {
		font-variant-numeric: tabular-nums;
	}
	.upnl.pos {
		color: var(--pos);
	}
	.upnl.neg {
		color: var(--neg);
	}
	.state-cell::before {
		display: inline-block;
		width: 6px;
		height: 6px;
		margin-right: 6px;
		border-radius: 50%;
		background: var(--faint);
		vertical-align: 1px;
		content: '';
	}
	.state-cell.ok::before {
		background: var(--accent);
	}
	.state-cell.warn::before {
		background: var(--warn);
	}
	.state-cell.bad::before {
		background: var(--neg);
	}
	.protection-evidence {
		display: block;
		color: var(--muted);
		font-size: var(--fs-xs);
	}
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
	.bot-head {
		display: flex;
		flex-wrap: wrap;
		align-items: flex-start;
		gap: 12px 16px;
		margin-bottom: 14px;
	}
	.bot-id {
		min-width: 0;
		flex: 1;
	}
	.title-row {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 10px;
	}
	.title-row h1 {
		margin: 0;
	}
	.pill {
		display: inline-flex;
		align-items: center;
		height: 24px;
		padding: 0 9px;
		border: 1px solid var(--line);
		border-radius: var(--radius-sm);
		background: var(--surface-2);
		color: var(--text);
		font-size: var(--fs-sm);
		text-decoration: none;
	}
	.pill:hover {
		border-color: var(--line-2);
	}
	.lede {
		margin-top: 4px;
	}
	.facts {
		display: flex;
		flex-wrap: wrap;
		gap: 4px 18px;
		margin: 6px 0 0;
		color: var(--faint);
		font-size: var(--fs-sm);
	}
	.facts b {
		color: var(--text);
		font-weight: 500;
	}
	.controls {
		display: flex;
		flex-wrap: wrap;
		gap: 8px;
	}
	.btn.danger {
		border-color: var(--danger-line);
		color: var(--neg);
	}
	.kpis {
		display: grid;
		grid-template-columns: repeat(4, minmax(0, 1fr));
		gap: 12px;
		margin-bottom: 16px;
	}
	.kpi {
		min-width: 0;
		padding: 14px 16px;
	}
	.kpi .label {
		color: var(--muted);
		font-size: var(--fs-sm);
		font-weight: 400;
	}
	.kpi .value {
		margin: 4px 0 0;
		color: var(--text);
		font-size: var(--fs-2xl);
		font-weight: 600;
		letter-spacing: -0.02em;
		overflow-wrap: anywhere;
	}
	.kpi .value.pos {
		color: var(--pos);
	}
	.kpi .value.neg {
		color: var(--neg);
	}
	.kpi .value.small {
		font-size: var(--fs-xl);
	}
	.kpi .delta {
		margin: 2px 0 0;
		color: var(--faint);
		font-size: var(--fs-sm);
	}
	.grid2 {
		display: grid;
		grid-template-columns: minmax(0, 1.5fr) minmax(0, 1fr);
		gap: 16px;
		margin-bottom: 16px;
	}
	.grid2.even {
		grid-template-columns: repeat(2, minmax(0, 1fr));
	}
	.card {
		margin-bottom: 16px;
	}
	.grid2 > .card,
	.kpis > .card {
		margin-bottom: 0;
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
	.pad {
		margin: 0;
		padding: 14px 16px;
	}
	.history-note {
		margin: 0;
		padding: 10px 16px;
		border-top: 1px dashed var(--line-2);
		color: var(--faint);
		font-size: var(--fs-sm);
	}
	.quiet {
		color: var(--muted);
	}
	.small {
		font-size: var(--fs-sm);
	}
	.muted {
		color: var(--muted);
	}
	.pos {
		color: var(--pos);
	}
	.neg,
	.problem {
		color: var(--neg);
	}
	.contract-note {
		margin: 0 0 12px;
		color: var(--warn);
		font-size: var(--fs-sm);
	}
	.kpi .contract-note {
		margin: 2px 0 0;
	}
	.problem-banner {
		margin: 0 0 12px;
		padding: 10px 14px;
		border: 1px solid var(--danger-line);
		border-radius: var(--radius-md);
		background: var(--danger-soft);
		color: var(--neg);
	}
	.breaker-row {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 10px 14px;
		margin-bottom: 14px;
		padding: 10px 14px;
		border: 1px solid var(--danger-line);
		border-radius: var(--radius-md);
		background: var(--danger-soft);
	}
	.breaker-row p {
		margin: 0;
	}
	.evidence-links {
		display: flex;
		flex-wrap: wrap;
		gap: 8px 16px;
		margin: 0 0 8px;
	}
	.pager {
		display: flex;
		align-items: center;
		gap: 10px;
		padding: 10px 16px;
		border-top: 1px solid var(--line);
		color: var(--faint);
		font-size: var(--fs-sm);
	}
	.pager button {
		padding: 5px 10px;
		border: 1px solid var(--line-2);
		border-radius: var(--radius-sm);
		background: var(--surface-2);
		color: var(--text);
		cursor: pointer;
	}
	.pager button:disabled {
		opacity: 0.45;
		cursor: not-allowed;
	}
	.table-scroll {
		overflow-x: auto;
	}
	table {
		width: 100%;
		border-collapse: collapse;
	}
	th,
	td {
		padding: 9px 16px;
		text-align: left;
		font-size: var(--fs-sm);
	}
	th.num,
	td.num {
		text-align: right;
	}
	td {
		border-top: 1px solid var(--line);
	}
	.mono {
		font-family: var(--font-mono);
	}
	.other-list {
		display: grid;
		gap: 8px;
		margin: 0 0 8px;
		padding: 0;
		list-style: none;
	}
	.disclosure summary {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 6px 12px;
		padding: 12px 16px;
		cursor: pointer;
		font-weight: 600;
	}
	.disclosure summary code {
		font-weight: 400;
		font-size: var(--fs-xs);
		word-break: break-all;
	}
	.disclosure[open] summary {
		border-bottom: 1px solid var(--line);
	}
	.kv {
		display: flex;
		flex-wrap: wrap;
		gap: 10px 28px;
		margin: 0 0 12px;
	}
	.kv dt {
		color: var(--faint);
		font-size: var(--fs-xs);
	}
	.kv dd {
		margin: 2px 0 0;
		font-family: var(--font-mono);
		font-size: var(--fs-sm);
	}
	.config-summary {
		display: grid;
		gap: 6px;
	}
	.config-summary p {
		margin: 0;
	}
	.config-section {
		margin-top: 4px;
		color: var(--muted);
		font-size: var(--fs-xs);
		text-transform: uppercase;
		letter-spacing: 0.06em;
	}
	.config-list {
		display: grid;
		gap: 2px;
		margin: 0;
		padding-left: 18px;
	}
	.warn-banner {
		display: flex;
		align-items: center;
		justify-content: space-between;
		margin-bottom: 14px;
		padding: 14px 17px;
		border: 1px solid var(--warn-line);
		border-radius: var(--radius-md);
		background: var(--warn-soft);
	}
	.warn-banner p {
		margin: 4px 0 0;
		color: var(--warn);
	}
	.ok-banner {
		margin-bottom: 14px;
		padding: 12px 17px;
		border: 1px solid var(--accent-line);
		border-radius: var(--radius-md);
		background: var(--accent-soft);
	}
	.ok-banner p {
		margin: 0;
		color: var(--muted);
	}
	@media (max-width: 1100px) {
		.kpis {
			grid-template-columns: repeat(2, minmax(0, 1fr));
		}
		.grid2,
		.grid2.even {
			grid-template-columns: minmax(0, 1fr);
		}
	}
	@media (max-width: 560px) {
		.kpis {
			grid-template-columns: minmax(0, 1fr);
		}
	}
</style>
