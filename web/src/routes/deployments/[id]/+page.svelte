<script lang="ts">
	import { resolve } from '$app/paths';
	import { page as pageState } from '$app/state';
	import DeploymentLifecycleDialog from '$lib/DeploymentLifecycleDialog.svelte';
	import {
		canOfferFlatten,
		drawdownIsCaveated,
		EVIDENCE_BOUNDED_NOTE,
		eligibility,
		exactVersionEvidenceLinks,
		fingerprintText,
		lifecycleAcceptedMessage,
		lastEvaluatedText,
		ledgerPerformanceText,
		marketLabel,
		otherVersionDeployments,
		performanceReportText,
		performanceCurrencySuffix,
		type LifecycleAction
	} from '$lib/deployment-detail';
	import {
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
	import { loadStrategyConfig, type StrategySourceState } from '$lib/strategy-config';

	const id = $derived(pageState.params.id ?? '');

	let deployment = $state<Deployment | null>(null);
	let inventory = $state<Deployment[]>([]);
	let loading = $state(true);
	let loadError = $state<string | null>(null);
	let stale = $state(false);
	let refreshError = $state<string | null>(null);

	// Exact-version configuration summary from the canonical source API.
	let strategyConfig = $state<StrategySourceState | null>(null);

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

	/** Exact-version rule/config summary; explicit unavailable state on failure. */
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

	async function confirmDialog(): Promise<void> {
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
			else if (action === 'resume') updated = await resumeDeployment(targetId);
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

<svelte:head><title>Deployment · ThyTrader</title></svelte:head>

<main>
	<a class="back-link" href={resolve('/deployments')}>← Deployments</a>
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
		<section class="detail-head">
			<p class="eyebrow">Deployment detail</p>
			<h1>{marketLabel(current.product_id)}</h1>
			<div class="identity-facts">
				<div><span>Mode</span><strong>{current.mode}</strong></div>
				<div>
					<span>Status</span><strong data-testid="deployment-status">{identity.status}</strong>
				</div>
				<div><span>Instruction</span><strong>{identity.instruction}</strong></div>
				<div>
					<span>Entry eligibility</span>
					<strong data-testid="deployment-eligibility">{identity.eligibility}</strong>
				</div>
				<div><span>Timeframe</span><strong>{current.timeframe ?? 'unknown'}</strong></div>
				<div><span>Revision</span><strong>{current.revision}</strong></div>
			</div>
			<div class="version-evidence">
				<span>Exact published configuration</span>
				<code data-testid="deployment-fingerprint">{fingerprintText(current)}</code>
				{#if strategyConfig === null}
					<p class="quiet" data-testid="strategy-config-loading">Loading configuration…</p>
				{:else if strategyConfig.kind === 'unavailable'}
					<p class="contract-note" data-testid="strategy-config-unavailable" role="status">
						Immutable configuration unavailable ({strategyConfig.reason}). The fingerprint above is
						the only identity shown; no other version was substituted.
					</p>
				{:else}
					<div class="config-summary" data-testid="strategy-config-summary">
						<p class="config-rule">{strategyConfig.summary.rule}</p>
						<dl class="config-facts">
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
		</section>

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
		{:else if actionError}
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

		<section class="evidence-grid">
			<div class="panel">
				<h2>Last evaluated bar</h2>
				<p>{lastEvaluatedText(current)}</p>
			</div>
			<div class="panel">
				<h2>Performance</h2>
				{#if performanceLoading && performanceReport === null}
					<p class="quiet" data-testid="performance-loading">Loading operator performance…</p>
				{:else if performanceError !== null}
					<p class="problem" data-testid="performance-error" role="status">
						Operator performance report unavailable ({performanceError}). Ledger summary:
						{ledgerPerformanceText(current)}
					</p>
				{:else if performancePayload}
					<p data-testid="deployment-performance">
						{performanceReportText(performancePayload)}
					</p>
					<p class="quiet provenance-note">
						Mode {performancePayload.mode} ·{performanceCurrencySuffix(
							performancePayload.currency
						).trim() === ''
							? ' quote currency unknown'
							: ` quoted in ${performancePayload.currency}`}
					</p>
					{#if drawdownIsCaveated(performancePayload)}
						<p class="quiet provenance-note" data-testid="performance-drawdown-caveat">
							Drawdown {(Number(performancePayload.maximum_drawdown_fraction) * 100).toFixed(2)}% is
							from fill-event marks, not a bar equity curve; it understates intra-bar drawdown.
						</p>
					{/if}
					{#each performanceReport?.partial_result_warnings ?? [] as warning (warning)}
						<p class="contract-note" role="status">{warning}</p>
					{/each}
				{:else}
					<p class="quiet">{ledgerPerformanceText(current)}</p>
				{/if}
			</div>
			<div class="panel">
				<h2>Capital</h2>
				<p>
					{#if current.capital && (current.capital.allocated_capital || current.capital.performance_equity)}
						allocated {current.capital.allocated_capital ?? 'unknown'} · equity {current.capital
							.performance_equity ?? 'unknown'}
						{#if current.mode === 'live'}
							· venue {current.capital.venue_available_quote ?? 'unknown'}
						{/if}
					{:else}
						No capital accounting on this snapshot.
					{/if}
				</p>
			</div>
			<div class="panel">
				<h2>Evidence links</h2>
				<p class="evidence-links">
					{#if evidenceLinks.length === 0}
						<span class="quiet">No version-scoped evidence for a discretionary deployment.</span>
					{:else}
						{#each evidenceLinks as link (link.href)}
							<!-- eslint-disable-next-line svelte/no-navigation-without-resolve -- dynamic cross-route link with query -->
							<a data-testid="evidence-link" href={link.href}>{link.label}</a>
						{/each}
					{/if}
					<a href={resolve('/journals')}>Trade journals</a>
					<a href={resolve('/audit')}>Audit log</a>
				</p>
				<p class="quiet bounded-note">{EVIDENCE_BOUNDED_NOTE}</p>
			</div>
		</section>

		<section class="panel">
			<h2>Positions &amp; protection</h2>
			{#if (current.positions ?? []).length === 0 && current.position === null}
				<p class="quiet">No open product books on this snapshot.</p>
			{:else}
				<!-- svelte-ignore a11y_no_noninteractive_tabindex -->
				<div class="table-scroll" tabindex="0" role="region" aria-label="Open positions">
					<table>
						<caption class="visually-hidden">Open positions with protection status</caption>
						<thead>
							<tr>
								<th scope="col">Product</th>
								<th scope="col">Side</th>
								<th scope="col">Quantity</th>
								<th scope="col">Entry</th>
								<th scope="col">Stop</th>
								<th scope="col">Target</th>
								<th scope="col">Protection</th>
							</tr>
						</thead>
						<tbody>
							{#each current.positions ?? [] as position (position.product_id)}
								<tr>
									<td>{position.product_id}</td>
									<td>{position.side ?? 'long'}</td>
									<td>{position.quantity}</td>
									<td>{position.entry_price}</td>
									<td>{position.stop_price}</td>
									<td>{position.target_price}</td>
									<td>{position.protection_status ?? 'unknown'}</td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
			{/if}
		</section>

		<section class="panel">
			<h2>Orders</h2>
			{#if orderError}
				<p class="problem" role="status" data-testid="orders-error">
					Order history could not be loaded ({orderError})
					<button
						type="button"
						class="bar-button"
						onclick={() => void loadOrdersPage(orderCursors[orderPageIndex], orderPageIndex)}
					>
						Retry
					</button>
				</p>
			{:else if orderLoading && orderPage.rows.length === 0}
				<p class="quiet" data-testid="orders-loading">Loading orders…</p>
			{:else if orderPage.rows.length === 0}
				<p class="quiet" data-testid="orders-empty">No orders recorded for this deployment.</p>
			{:else}
				<!-- svelte-ignore a11y_no_noninteractive_tabindex -->
				<div class="table-scroll" tabindex="0" role="region" aria-label="Order history">
					<table>
						<caption class="visually-hidden">Order history</caption>
						<thead>
							<tr>
								<th scope="col">Product</th>
								<th scope="col">Side</th>
								<th scope="col">Kind</th>
								<th scope="col">Quantity</th>
								<th scope="col">Status</th>
								<th scope="col">Reject reason</th>
							</tr>
						</thead>
						<tbody>
							{#each orderPage.rows as order (order.id)}
								<tr>
									<td>{order.product_id || current.product_id}</td>
									<td>{order.side}</td>
									<td>{order.kind}</td>
									<td>{order.quantity}</td>
									<td>{order.status}</td>
									<td>{order.reject_reason ?? '—'}</td>
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
		</section>

		<section class="panel">
			<h2>Fills</h2>
			{#if fillError}
				<p class="problem" role="status" data-testid="fills-error">
					Fill history could not be loaded ({fillError})
					<button
						type="button"
						class="bar-button"
						onclick={() => void loadFillsPage(fillCursors[fillPageIndex], fillPageIndex)}
					>
						Retry
					</button>
				</p>
			{:else if fillLoading && fillPage.rows.length === 0}
				<p class="quiet" data-testid="fills-loading">Loading fills…</p>
			{:else if fillPage.rows.length === 0}
				<p class="quiet" data-testid="fills-empty">No fills recorded for this deployment.</p>
			{:else}
				<!-- svelte-ignore a11y_no_noninteractive_tabindex -->
				<div class="table-scroll" tabindex="0" role="region" aria-label="Fill history">
					<table>
						<caption class="visually-hidden">Fill history</caption>
						<thead>
							<tr>
								<th scope="col">Product</th>
								<th scope="col">Time</th>
								<th scope="col">Quantity</th>
								<th scope="col">Price</th>
								<th scope="col">Fee</th>
							</tr>
						</thead>
						<tbody>
							{#each fillPage.rows as fill (fill.id)}
								<tr>
									<td>{fill.product_id || current.product_id}</td>
									<td>{fill.filled_at}</td>
									<td>{fill.quantity}</td>
									<td>{fill.price}</td>
									<td>{fill.fee}</td>
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

		<section class="panel lifecycle-panel">
			<h2>Lifecycle</h2>
			{#if controlsAvailable}
				<div class="actions">
					{#if current.status === 'running'}
						<button
							type="button"
							class="bar-button"
							disabled={mutating}
							onclick={() => openDialog('pause')}>Pause entries…</button
						>
					{/if}
					{#if current.status === 'paused'}
						<button
							type="button"
							class="bar-button"
							disabled={mutating}
							onclick={() => openDialog('resume')}>Resume entries…</button
						>
					{/if}
					{#if current.status !== 'stopped'}
						<button
							type="button"
							class="bar-button bar-danger"
							disabled={mutating}
							onclick={() => openDialog('stop')}>Stop…</button
						>
					{/if}
					{#if canOfferFlatten(current)}
						<button
							type="button"
							class="bar-button bar-danger"
							disabled={mutating}
							onclick={() => openDialog('flatten')}>Flatten remaining exposure…</button
						>
					{/if}
				</div>
				<p class="quiet">Confirmations name the exact consequence before any command is sent.</p>
			{:else}
				<p class="contract-note" data-testid="controls-blocked-note">
					{controlsBlocked
						? 'Lifecycle controls are disabled until this deployment is refreshed successfully. An earlier action had an unknown outcome or the snapshot is stale; refresh to re-enable.'
						: 'Lifecycle controls are unavailable because this server did not return the current lifecycle contract. Values are shown read-only; nothing is inferred.'}
				</p>
			{/if}
			{#if current.mismatch_detail}
				<p class="problem" role="alert">{current.mismatch_detail}</p>
			{/if}
			{#if current.daily_loss_latched || current.drawdown_latched}
				<div class="breaker-row">
					<p class="problem" role="status">
						{[
							current.daily_loss_latched ? 'Daily-loss breaker latched' : null,
							current.drawdown_latched ? 'Drawdown breaker latched' : null
						]
							.filter(Boolean)
							.join(' · ')}
					</p>
					<!-- Confirmation required: the reset dialog names the consequence
					     before any POST is sent. -->
					<button
						type="button"
						class="bar-button"
						disabled={mutating || controlsBlocked}
						data-testid="reset-breakers-button"
						onclick={() => openDialog('reset-breakers')}>Reset breaker latches…</button
					>
				</div>
			{/if}
		</section>

		<section class="panel" aria-label="Other deployments for this strategy">
			<h2>Other deployments for this strategy</h2>
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
				<p class="quiet">
					Different published versions; their evidence is not mixed into this page.
				</p>
			{/if}
		</section>
	{/if}
</main>

{#if dialogAction !== null && current}
	<DeploymentLifecycleDialog
		deployment={current}
		action={dialogAction}
		bind:stopWithFlatten
		{mutating}
		{actionError}
		oncancel={closeDialog}
		onconfirm={() => void confirmDialog()}
	/>
{/if}

<style>
	.back-link {
		display: inline-block;
		margin-bottom: 18px;
		color: #9fd9ff;
		font-size: 13px;
		text-decoration: none;
	}
	.back-link:hover {
		text-decoration: underline;
	}
	.detail-head {
		margin-bottom: 24px;
	}
	.identity-facts {
		display: flex;
		flex-wrap: wrap;
		gap: 10px 30px;
		margin: 16px 0;
	}
	.identity-facts span {
		display: block;
		color: #7d8a8d;
		font-size: 10px;
		text-transform: uppercase;
		letter-spacing: 0.07em;
	}
	.identity-facts strong {
		font:
			500 13px ui-monospace,
			SFMono-Regular,
			Consolas,
			monospace;
		color: #dce4e5;
	}
	.version-evidence {
		display: flex;
		align-items: baseline;
		flex-wrap: wrap;
		gap: 10px 14px;
		border: 1px solid #223033;
		border-radius: 8px;
		background: #0d1416;
		padding: 12px 14px;
	}
	.version-evidence > span {
		color: #7d8a8d;
		font-size: 10px;
		text-transform: uppercase;
		letter-spacing: 0.07em;
	}
	.version-evidence code {
		font-size: 12px;
		word-break: break-all;
	}
	.config-summary {
		flex-basis: 100%;
		display: grid;
		gap: 6px;
		font-size: 13px;
		color: #d8e1e2;
	}
	.config-summary p {
		margin: 0;
	}
	.config-rule {
		color: #e9edf1;
	}
	.config-facts {
		display: flex;
		flex-wrap: wrap;
		gap: 8px 26px;
		margin: 4px 0;
	}
	.config-facts div {
		display: grid;
	}
	.config-facts dt {
		color: #7d8a8d;
		font-size: 10px;
		text-transform: uppercase;
		letter-spacing: 0.07em;
	}
	.config-facts dd {
		margin: 0;
		font:
			500 12px ui-monospace,
			SFMono-Regular,
			Consolas,
			monospace;
		color: #dce4e5;
	}
	.config-section {
		color: #aeb9bb;
		font-size: 11px;
		text-transform: uppercase;
		letter-spacing: 0.06em;
		margin-top: 4px;
	}
	.config-list {
		margin: 0;
		padding-left: 18px;
		display: grid;
		gap: 2px;
	}
	.evidence-grid {
		display: grid;
		grid-template-columns: repeat(2, minmax(0, 1fr));
		gap: 12px;
		margin-bottom: 12px;
	}
	.panel {
		border: 1px solid #232b2d;
		background: linear-gradient(145deg, rgba(20, 26, 28, 0.95), rgba(12, 16, 18, 0.95));
		border-radius: 13px;
		padding: 18px 20px;
		margin-bottom: 12px;
	}
	.panel h2 {
		margin: 0 0 10px;
		font-size: 12px;
		color: #aeb9bb;
		text-transform: uppercase;
		letter-spacing: 0.06em;
	}
	.panel p {
		margin: 0;
		font-size: 13px;
		color: #d8e1e2;
	}
	.quiet {
		color: #8d999c;
	}
	.problem {
		color: #f0a3a3;
		font-size: 13px;
	}
	.contract-note {
		color: #b39b72;
		font-size: 12px;
	}
	.provenance-note,
	.bounded-note {
		margin-top: 6px;
		font-size: 12px;
	}
	.evidence-links {
		display: flex;
		gap: 16px;
		flex-wrap: wrap;
	}
	.evidence-links a {
		color: #9fd9ff;
		text-decoration: none;
	}
	.evidence-links a:hover {
		text-decoration: underline;
	}
	.pager {
		display: flex;
		align-items: center;
		gap: 10px;
		margin-top: 10px;
		color: #7d8a8d;
		font-size: 12px;
	}
	.table-scroll {
		overflow-x: auto;
	}
	table {
		width: 100%;
		border-collapse: collapse;
		font-size: 12px;
	}
	th,
	td {
		text-align: left;
		padding: 6px 10px 6px 0;
		border-bottom: 1px solid #232d2e;
	}
	th {
		color: #aeb9bb;
		font-weight: 500;
		font-size: 11px;
	}
	.actions {
		display: flex;
		gap: 8px;
		flex-wrap: wrap;
	}
	.breaker-row {
		display: flex;
		align-items: center;
		gap: 14px;
		flex-wrap: wrap;
	}
	.other-list {
		margin: 0;
		padding: 0;
		list-style: none;
		display: grid;
		gap: 8px;
	}
	.other-list a {
		color: #9fd9ff;
		font-size: 13px;
		text-decoration: none;
	}
	.other-list a:hover {
		text-decoration: underline;
	}
	.warn-banner {
		display: flex;
		align-items: center;
		justify-content: space-between;
		padding: 14px 17px;
		border-radius: 10px;
		border: 1px solid #6b5a2c;
		background: #241d10;
		margin-bottom: 18px;
	}
	.warn-banner p {
		margin: 4px 0 0;
		color: #d8c79a;
		font-size: 13px;
	}
	.ok-banner {
		padding: 12px 17px;
		border-radius: 10px;
		border: 1px solid #315849;
		background: #10241d;
		margin-bottom: 18px;
	}
	.ok-banner p {
		margin: 0;
		color: #8ea79f;
		font-size: 13px;
	}
	.visually-hidden {
		position: absolute;
		width: 1px;
		height: 1px;
		overflow: hidden;
		clip: rect(0 0 0 0);
		white-space: nowrap;
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
	@media (max-width: 800px) {
		.evidence-grid {
			grid-template-columns: 1fr;
		}
	}
</style>
