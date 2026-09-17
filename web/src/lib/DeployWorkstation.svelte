<script lang="ts">
	import {
		bookTotalsReconcile,
		capitalSummary,
		canonicalBooks,
		canonicalPositions,
		createDeployment,
		fillProductId,
		listDeployments,
		orderProductId,
		pauseDeployment,
		resumeDeployment,
		stopDeployment,
		type Deployment,
		type DeploymentInstrumentRuntime,
		type DeploymentPosition
	} from '$lib/deployments';
	import {
		PAPER_DEFAULT_MAKER_FEE_RATE,
		PAPER_DEFAULT_TAKER_FEE_RATE,
		PAPER_FEE_ENGINE_NOTE,
		fetchFeeProfile,
		readResearchFeeSuggestion,
		shouldPrefillPaperFeeRates
	} from '$lib/fees';
	import {
		extraIndicatorTimeframes,
		publishedVersionsFor,
		type BuilderModel,
		type StrategyLibraryEntry
	} from '$lib/strategies';

	let {
		entry,
		model,
		onChanged
	}: {
		entry: StrategyLibraryEntry;
		model: BuilderModel | null;
		onChanged?: () => void;
	} = $props();

	let strategyDeployments = $state<Deployment[]>([]);
	let deployLoading = $state(false);
	let deploying = $state(false);
	let deployError = $state<string | null>(null);
	let deployFingerprint = $state('');
	let deployMode = $state<'paper' | 'live'>('paper');
	let deployCash = $state('10000');
	let deployMakerFee = $state(PAPER_DEFAULT_MAKER_FEE_RATE);
	let deployTakerFee = $state(PAPER_DEFAULT_TAKER_FEE_RATE);
	let deployFeeFieldsTouched = $state(false);
	let feeSuggestionRequestId = 0;

	const publishedVersions = $derived(publishedVersionsFor(entry));

	$effect(() => {
		const strategyId = entry.strategy_id;
		deployFingerprint = publishedVersionsFor(entry).at(-1)?.strategy_fingerprint ?? '';
		deployMode = 'paper';
		deployCash = '10000';
		deployMakerFee = PAPER_DEFAULT_MAKER_FEE_RATE;
		deployTakerFee = PAPER_DEFAULT_TAKER_FEE_RATE;
		deployFeeFieldsTouched = false;
		deployError = null;
		void loadStrategyDeployments();
		void loadFeeSuggestion();
		void strategyId;
	});

	async function loadFeeSuggestion(): Promise<void> {
		const requestId = ++feeSuggestionRequestId;
		try {
			const profile = await fetchFeeProfile();
			if (requestId !== feeSuggestionRequestId) return;
			const suggestion = readResearchFeeSuggestion(profile);
			if (
				shouldPrefillPaperFeeRates({
					makerFeeRate: deployMakerFee,
					takerFeeRate: deployTakerFee,
					touched: deployFeeFieldsTouched,
					suggestion
				}) &&
				suggestion !== null
			) {
				deployMakerFee = suggestion.makerFeeRate;
				deployTakerFee = suggestion.takerFeeRate;
			}
		} catch {
			/* documented 0.001 / 0.002 defaults remain */
		}
	}

	function positionForBook(
		deployment: Deployment,
		book: DeploymentInstrumentRuntime
	): DeploymentPosition | undefined {
		return canonicalPositions(deployment).find((item) => item.product_id === book.product_id);
	}

	function protectionForBook(
		deployment: Deployment,
		book: DeploymentInstrumentRuntime
	): string {
		if (book.phase === 'flat') {
			return 'flat';
		}
		const position = positionForBook(deployment, book);
		return position?.protection_status ?? 'unknown';
	}

	async function loadStrategyDeployments(): Promise<void> {
		const current = entry;
		deployLoading = true;
		try {
			const deployments = await listDeployments();
			if (entry.strategy_id !== current.strategy_id) return;
			strategyDeployments = deployments.filter(
				(deployment) => deployment.strategy_id === current.strategy_id
			);
		} catch (caught) {
			if (entry.strategy_id !== current.strategy_id) return;
			deployError = caught instanceof Error ? caught.message : 'Could not load deployments.';
		} finally {
			if (entry.strategy_id === current.strategy_id) {
				deployLoading = false;
			}
		}
	}

	async function deployStrategy(): Promise<void> {
		if (deployMode === 'live') {
			const selectedVersion = publishedVersions.find(
				(version) => version.strategy_fingerprint === deployFingerprint
			);
			const versionLabel = selectedVersion
				? `v${selectedVersion.version}`
				: deployFingerprint.slice(0, 18);
			const timeframe = model?.timeframe ?? entry.timeframe;
			const confirmed = window.confirm(
				`ARM LIVE TRADING on Coinbase?\n\nThis will place REAL spot orders using your Coinbase API keys.\n\nStrategy: ${entry.name}\nVersion: ${versionLabel}\nFingerprint: ${deployFingerprint.slice(0, 32)}...\nTimeframe: ${timeframe}\nMarket: ${entry.product_id}\n\nYou are solely responsible for all trades and market risk.`
			);
			if (!confirmed) return;
		}
		deploying = true;
		deployError = null;
		try {
			await createDeployment({
				strategy_fingerprint: deployFingerprint,
				mode: deployMode,
				paper_starting_cash: deployMode === 'paper' ? deployCash : undefined,
				maker_fee_rate: deployMode === 'paper' ? deployMakerFee : undefined,
				taker_fee_rate: deployMode === 'paper' ? deployTakerFee : undefined
			});
			await loadStrategyDeployments();
			onChanged?.();
		} catch (caught) {
			deployError = caught instanceof Error ? caught.message : 'Could not start the deployment.';
		} finally {
			deploying = false;
		}
	}

	async function changeDeployment(id: string, action: 'pause' | 'resume' | 'stop'): Promise<void> {
		const deployment = strategyDeployments.find((item) => item.id === id);
		if (action === 'stop') {
			const confirmed = window.confirm(
				'Stop this deployment permanently? Resting orders will be canceled.'
			);
			if (!confirmed) return;
		}
		if (action === 'resume' && deployment?.mode === 'live') {
			const confirmed = window.confirm(
				'RESUME LIVE TRADING on Coinbase?\n\nThis will RE-ARM real spot order submission using your Coinbase API keys.\n\nYou are solely responsible for all trades and market risk.'
			);
			if (!confirmed) return;
		}
		deployError = null;
		try {
			if (action === 'pause') await pauseDeployment(id);
			else if (action === 'resume') await resumeDeployment(id);
			else await stopDeployment(id);
			await loadStrategyDeployments();
			onChanged?.();
		} catch (caught) {
			deployError = caught instanceof Error ? caught.message : 'Could not update the deployment.';
		}
	}
</script>

<div class="view-block">
	<h3>Deploy</h3>
	<p class="view-note">
		Starts the strategy runtime on closed candles. <strong>Paper:</strong> any ingested venue clock
		(strategy clock); simulates maker fills. <strong>Live:</strong> the same clocks; places real Coinbase
		spot orders. Sub-hour live requires a connected user-order feed. Setting Coinbase credentials does
		not arm live trading.
	</p>
	{#if model?.htf_filter}
		<p class="view-note">
			This version ANDs last-completed {model.htf_filter.timeframe} HTF bars with LTF entry. Paper and
			live load live complete-only HTF candles; missing coverage pauses.
		</p>
	{/if}
	{#if model && extraIndicatorTimeframes(model.indicators, model.timeframe).length > 0}
		<p class="view-note">
			This version also evaluates last-completed {extraIndicatorTimeframes(
				model.indicators,
				model.timeframe
			).join(', ')} indicator bars. Paper and live load those complete-only candles; missing coverage
			pauses.
		</p>
	{/if}
	{#if publishedVersions.length === 0}
		<p class="view-note">Publish this strategy before deploying.</p>
	{:else}
		<div class="launch-grid">
			<label
				>Published version
				<select bind:value={deployFingerprint}>
					{#each publishedVersions as version (version.strategy_fingerprint)}
						<option value={version.strategy_fingerprint}>Version {version.version}</option>
					{/each}
				</select></label
			>
			<label
				>Mode
				<select bind:value={deployMode}>
					<option value="paper">Paper</option>
					<option value="live">Live</option>
				</select></label
			>
		</div>
		{#if deployMode === 'paper'}
			<label class="deploy-cash"
				>Paper starting cash (USD)
				<input bind:value={deployCash} /></label
			>
			<div class="launch-grid">
				<label
					>Maker fee rate
					<input
						inputmode="decimal"
						bind:value={deployMakerFee}
						oninput={() => (deployFeeFieldsTouched = true)}
					/></label
				>
				<label
					>Taker fee rate
					<input
						inputmode="decimal"
						bind:value={deployTakerFee}
						oninput={() => (deployFeeFieldsTouched = true)}
					/></label
				>
			</div>
			<p class="view-note">{PAPER_FEE_ENGINE_NOTE}</p>
		{/if}
		<button
			class="launch-button"
			class:live-danger={deployMode === 'live'}
			type="button"
			disabled={deploying || !deployFingerprint}
			onclick={() => void deployStrategy()}
		>
			{deploying ? 'Starting…' : deployMode === 'live' ? 'Arm live trading…' : 'Start deployment'}
		</button>
		{#if deployError}
			<p class="view-problem" role="alert">{deployError}</p>
		{/if}
	{/if}
</div>
<div class="view-block">
	<h3>Runtime</h3>
	{#if deployLoading}
		<p class="view-note">Loading deployments…</p>
	{:else if strategyDeployments.length === 0}
		<p class="view-note">No deployments yet.</p>
	{:else}
		{#each strategyDeployments as deployment (deployment.id)}
			<div class="version-block">
				<h4>{deployment.mode} · {deployment.status} · {deployment.phase}</h4>
				<p>
					Ledger cash {deployment.cash}
					{#if capitalSummary(deployment)}
						· {capitalSummary(deployment)}
					{/if}
					{#if deployment.mode === 'paper' && deployment.maker_fee_rate && deployment.taker_fee_rate}
						· paper fees {deployment.maker_fee_rate}/{deployment.taker_fee_rate}
					{/if}
					{#if deployment.last_signal}
						· last signal {deployment.last_signal}
					{/if}
					{#if deployment.last_evaluated_bar}
						· bar {deployment.last_evaluated_bar}
					{/if}
				</p>
				{#if deployment.mismatch_detail}
					<p class="view-problem" role="alert">{deployment.mismatch_detail}</p>
				{/if}
				{#if !bookTotalsReconcile(deployment)}
					<p class="view-problem" role="alert">
						Book totals do not reconcile to the positions, working orders, and fills on this
						snapshot.
					</p>
				{/if}
				{#each canonicalBooks(deployment) as book (book.product_id)}
					{@const position = positionForBook(deployment, book)}
					<p>
						{book.product_id} · {book.phase}
						{#if position}
							· {position.side ?? 'long'}
							{position.quantity} @ {position.entry_price} · stop
							{position.stop_price} · target {position.target_price}
							{#if position.trail_extreme}
								· trail {position.trail_extreme}
							{/if}
							· {position.protection_status ?? 'unknown'}
							{#if position.compatibility_focus}
								· compatibility focus (not the full inventory)
							{/if}
						{:else}
							· no open book · {protectionForBook(deployment, book)}
						{/if}
					</p>
				{/each}
				{#if deployment.orders.length > 0}
					<table class="results-table" aria-label="Open and recent orders">
						<thead>
							<tr>
								<th scope="col">Product</th>
								<th scope="col">Side</th>
								<th scope="col">Qty</th>
								<th scope="col">Kind</th>
								<th scope="col">Status</th>
								<th scope="col">Price</th>
								<th scope="col">Reject</th>
							</tr>
						</thead>
						<tbody>
							{#each deployment.orders as order (order.id)}
								<tr>
									<td>{orderProductId(deployment, order)}</td>
									<td>{order.side}</td>
									<td>{order.quantity}</td>
									<td>{order.kind}</td>
									<td>{order.status}</td>
									<td>{order.price ?? '—'}</td>
									<td>{order.reject_reason ?? '—'}</td>
								</tr>
							{/each}
						</tbody>
					</table>
				{/if}
				{#if deployment.fills.length > 0}
					<table class="results-table" aria-label="Fills">
						<thead>
							<tr>
								<th scope="col">Product</th>
								<th scope="col">Time</th>
								<th scope="col">Qty</th>
								<th scope="col">Price</th>
								<th scope="col">Fee</th>
							</tr>
						</thead>
						<tbody>
							{#each deployment.fills as fill (fill.id)}
								<tr>
									<td>{fillProductId(deployment, fill)}</td>
									<td>{fill.filled_at}</td>
									<td>{fill.quantity}</td>
									<td>{fill.price}</td>
									<td>{fill.fee}</td>
								</tr>
							{/each}
						</tbody>
					</table>
				{/if}
				<div class="version-actions">
					{#if deployment.status === 'running'}
						<button
							class="bar-button"
							type="button"
							onclick={() => void changeDeployment(deployment.id, 'pause')}>Pause</button
						>
					{/if}
					{#if deployment.status === 'paused'}
						<button
							class="bar-button"
							type="button"
							onclick={() => void changeDeployment(deployment.id, 'resume')}>Resume</button
						>
					{/if}
					{#if deployment.status !== 'stopped'}
						<button
							class="bar-button bar-danger"
							type="button"
							onclick={() => void changeDeployment(deployment.id, 'stop')}>Stop</button
						>
					{/if}
				</div>
			</div>
		{/each}
	{/if}
</div>

<style>
	.view-block {
		display: grid;
		gap: 12px;
		margin-bottom: 28px;
	}
	.view-block h3 {
		margin: 0 0 6px;
		font-size: 12px;
		color: #aeb9bb;
		text-transform: uppercase;
		letter-spacing: 0.06em;
	}
	.view-block p {
		margin: 0;
		font-size: 13px;
		color: #d8e1e2;
	}
	.view-note {
		color: #aeb9bb;
		font-size: 13px;
	}
	.view-problem {
		color: #f0a3a3;
		font-size: 13px;
	}
	.launch-grid {
		display: grid;
		grid-template-columns: repeat(2, minmax(0, 1fr));
		gap: 10px;
	}
	.launch-grid label,
	.deploy-cash {
		display: grid;
		gap: 4px;
		font-size: 11px;
	}
	.deploy-cash {
		margin: 10px 0;
	}
	.launch-grid input,
	.launch-grid select,
	.deploy-cash input {
		border: 1px solid #303a3c;
		border-radius: 7px;
		background: #101617;
		color: #edf3f3;
		padding: 7px 9px;
		font: inherit;
		font-size: 12px;
		width: 100%;
	}
	.launch-button {
		justify-self: start;
		border: none;
		border-radius: 8px;
		background: #2f6f52;
		color: #eafff3;
		padding: 9px 14px;
		font: inherit;
		font-size: 13px;
		cursor: pointer;
	}
	.launch-button.live-danger {
		background: #8c3636;
		color: #ffe0e0;
		font-weight: 600;
	}
	.launch-button.live-danger:hover:not(:disabled) {
		background: #a54040;
	}
	.launch-button:disabled {
		opacity: 0.55;
		cursor: default;
	}
	.version-block {
		border: 1px solid #232d2e;
		border-radius: 8px;
		padding: 10px 12px;
		margin-bottom: 10px;
	}
	.version-block h4 {
		margin: 0 0 8px;
		font-size: 11px;
		color: #aeb9bb;
		text-transform: uppercase;
		letter-spacing: 0.05em;
	}
	.results-table {
		width: 100%;
		border-collapse: collapse;
		font-size: 12px;
	}
	.results-table th,
	.results-table td {
		text-align: left;
		padding: 5px 8px 5px 0;
		border-bottom: 1px solid #232d2e;
	}
	.results-table th {
		color: #aeb9bb;
		font-weight: 500;
		font-size: 11px;
	}
	.version-actions {
		display: flex;
		flex-wrap: wrap;
		gap: 6px;
		margin-top: 8px;
	}
	.bar-button {
		border: 1px solid #303a3c;
		background: #151b1d;
		color: #dce4e5;
		border-radius: 8px;
		padding: 6px 10px;
		font: inherit;
		font-size: 12px;
		cursor: pointer;
	}
	.bar-danger {
		color: #f0a3a3;
		border-color: #5c3232;
	}
	@media (max-width: 640px) {
		.launch-grid {
			grid-template-columns: 1fr;
		}
	}
</style>
