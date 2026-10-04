<script lang="ts">
	import { onMount } from 'svelte';
	import { resolve } from '$app/paths';
	import PageHead from '$lib/PageHead.svelte';
	import StageChooser from '$lib/workspace/StageChooser.svelte';
	import { fetchStrategyPage, type StrategyLibraryEntry } from '$lib/strategies';
	import {
		researchRequest,
		type CampaignRecord,
		type EconomicPreflight
	} from '$lib/research-campaigns';

	let campaigns: CampaignRecord[] = $state([]);
	let strategies: StrategyLibraryEntry[] = $state([]);
	let selected: string[] = $state([]);
	let error = $state('');
	let busy = $state(false);
	let ready = $state(false);
	let name = $state('');
	let kind: 'historical' | 'prospective' = $state('historical');
	let start = $state('');
	let end = $state('');
	let deadline = $state('');
	let capital = $state('1000');
	let maker = $state('0.005');
	let taker = $state('0.009');
	let minimumTrades = $state(10);
	let minimumReturn = $state('0');
	let maximumDrawdown = $state('0.15');
	let stressed = $state(false);
	let latency = $state(0);
	let penetration = $state('0');
	let fillFraction = $state('1');
	let entry = $state('100');
	let stop = $state('98');
	let target = $state('100.5');
	let quantity = $state('1');
	let side: 'long' | 'short' = $state('long');
	let economics: EconomicPreflight | null = $state(null);

	async function reload(): Promise<void> {
		try {
			campaigns = await researchRequest<CampaignRecord[]>('campaigns?limit=20');
		} catch (caught) {
			error = caught instanceof Error ? caught.message : 'Campaigns unavailable.';
		}
	}
	onMount(() => {
		ready = true;
		void reload();
		void fetchStrategyPage(100)
			.then((page) => {
				strategies = page.entries;
			})
			.catch(() => {
				error = 'Could not load strategies.';
			});
	});
	async function create(): Promise<void> {
		busy = true;
		error = '';
		try {
			await researchRequest<CampaignRecord>(
				'campaigns',
				{
					name,
					kind,
					deadline: `${deadline}:00Z`,
					gates: {
						minimum_trades: minimumTrades,
						minimum_net_return_fraction: minimumReturn,
						maximum_drawdown_fraction: maximumDrawdown
					},
					cases: selected.map((id, index) => ({
						key: `case-${index + 1}`,
						request: {
							strategy_id: id,
							evaluation_start: `${start}:00Z`,
							evaluation_end: `${end}:00Z`,
							initial_quote_balance: capital,
							maker_fee_rate: maker,
							taker_fee_rate: taker,
							fixed_slippage_bps: '5',
							spread_bps: '10',
							...(stressed
								? {
										execution_stress: {
											entry_latency_bars: latency,
											maker_penetration_bps: penetration,
											entry_fill_fraction: fillFraction
										}
									}
								: {})
						}
					}))
				},
				true
			);
			name = '';
			await reload();
		} catch (caught) {
			error = caught instanceof Error ? caught.message : 'Could not freeze campaign.';
		} finally {
			busy = false;
		}
	}
	async function downloadCsv(id: string): Promise<void> {
		try {
			const response = await fetch(`/api/v1/research/campaigns/${id}/export`);
			if (!response.ok) throw new Error('Campaign export failed.');
			const href = URL.createObjectURL(await response.blob());
			const anchor = document.createElement('a');
			anchor.href = href;
			anchor.download = `campaign-${id}.csv`;
			anchor.click();
			URL.revokeObjectURL(href);
		} catch (caught) {
			error = caught instanceof Error ? caught.message : 'Campaign export failed.';
		}
	}
	async function preflight(): Promise<void> {
		error = '';
		try {
			economics = await researchRequest<EconomicPreflight>('economics', {
				side,
				entry_price: entry,
				stop_price: stop,
				target_price: target || null,
				quantity,
				maker_fee_rate: maker,
				taker_fee_rate: taker,
				fixed_slippage_bps: '5',
				spread_bps: '10'
			});
		} catch (caught) {
			error = caught instanceof Error ? caught.message : 'Economics unavailable.';
		}
	}
</script>

<PageHead title="Research campaigns" />
<main>
	<StageChooser title="Research" stage="Test" />
	<header>
		<h2>Research campaigns</h2>
		<p>
			Freeze rules, windows, costs, and validation gates. Passing research does not deploy a bot.
		</p>
		<a href={resolve('/backtests')}>Backtest results</a>
	</header>
	{#if error}<p role="alert">{error}</p>{/if}
	<section aria-labelledby="freeze-title">
		<h2 id="freeze-title">Freeze a campaign</h2>
		<form
			onsubmit={(event) => {
				event.preventDefault();
				void create();
			}}
		>
			<label>Name <input bind:value={name} required maxlength="160" /></label>
			<label
				>Type <select bind:value={kind}
					><option value="historical">Historical comparison</option><option value="prospective"
						>Prospective validation</option
					></select
				></label
			>
			<label
				>Strategies (select one or more) <select multiple bind:value={selected} required
					>{#each strategies as strategy (strategy.strategy_id)}<option value={strategy.strategy_id}
							>{strategy.name}</option
						>{/each}</select
				></label
			>
			<label>Start (UTC) <input type="datetime-local" bind:value={start} required /></label>
			<label>End (UTC) <input type="datetime-local" bind:value={end} required /></label>
			<label>Deadline (UTC) <input type="datetime-local" bind:value={deadline} required /></label>
			<label>Starting quote capital <input bind:value={capital} required /></label>
			<label>Maker fee fraction <input bind:value={maker} required /></label>
			<label>Taker fee fraction <input bind:value={taker} required /></label>
			<label
				>Minimum closed trades <input
					type="number"
					min="1"
					bind:value={minimumTrades}
					required
				/></label
			>
			<label>Net return hurdle (fraction) <input bind:value={minimumReturn} required /></label>
			<label>Maximum drawdown (fraction) <input bind:value={maximumDrawdown} required /></label>
			<label><input type="checkbox" bind:checked={stressed} /> Stress execution assumptions</label>
			{#if stressed}
				<label
					>Additional entry delay (bars) <input
						type="number"
						min="0"
						max="100"
						bind:value={latency}
					/></label
				>
				<label>Required maker penetration (bps) <input bind:value={penetration} /></label>
				<label>Entry fill fraction <input bind:value={fillFraction} /></label>
				<p>
					One partial entry fill cancels its remainder. These are candle-based stresses, not
					observed queue behavior.
				</p>
			{/if}
			<p>
				Uses 5 bps taker slippage and 10 bps total spread. Prospective windows must begin after
				freezing; the research worker waits for complete data.
			</p>
			<button disabled={!ready || busy || selected.length === 0}
				>{busy ? 'Freezing…' : 'Freeze and authorize research'}</button
			>
		</form>
	</section>
	<section aria-labelledby="campaign-title">
		<h2 id="campaign-title">Frozen campaigns</h2>
		<button onclick={() => void reload()}>Refresh view</button>
		{#each campaigns as campaign (campaign.manifest.campaign_id)}
			<article>
				<h3>{campaign.manifest.name}</h3>
				<p>
					{campaign.manifest.kind} · deadline {campaign.manifest.deadline} · minimum {campaign
						.manifest.gates.minimum_trades} trades
				</p>
				<button onclick={() => void downloadCsv(campaign.manifest.campaign_id)}>Export CSV</button>
				<details>
					<summary>{campaign.cases.length} frozen cases</summary>
					<ul>
						{#each campaign.cases as item (item.key)}<li>
								{item.key}: {item.status} — {item.detail}
							</li>{/each}
					</ul>
					<code>{campaign.manifest_fingerprint}</code>
				</details>
			</article>
		{:else}<p>No frozen campaigns yet.</p>{/each}
	</section>
	<section aria-labelledby="economic-title">
		<h2 id="economic-title">Economic preflight</h2>
		<p>
			Uses the maker and taker assumptions above, including both fees. This calculation places no
			order.
		</p>
		<form
			onsubmit={(event) => {
				event.preventDefault();
				void preflight();
			}}
		>
			<label
				>Side <select bind:value={side}
					><option value="long">Long</option><option value="short">Short</option></select
				></label
			>
			<label>Entry price <input bind:value={entry} /></label><label
				>Stop <input bind:value={stop} /></label
			><label>Target (optional) <input bind:value={target} /></label><label
				>Quantity <input bind:value={quantity} /></label
			>
			<button disabled={!ready}>Calculate economics</button>
		</form>
		{#if economics}<p>
				Net target PnL: {economics.net_target_quote_pnl ?? 'No declared target'} · Net stop PnL: {economics.net_stop_quote_pnl}
				· Maker break-even price: {economics.maker_break_even_price}
			</p>
			<p>
				{economics.target_clears_costs === true
					? 'Target clears both maker fees.'
					: 'Target does not clear modeled costs or is absent.'}
			</p>{/if}
	</section>
</main>

<style>
	main {
		max-width: 1100px;
		margin: auto;
		padding: 1.5rem;
	}
	section {
		margin-top: 2rem;
		padding: 1.25rem;
		border: 1px solid var(--border, #888);
		border-radius: 8px;
	}
	form {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(230px, 1fr));
		gap: 1rem;
	}
	label {
		display: flex;
		flex-direction: column;
		gap: 0.4rem;
	}
	input,
	select,
	button {
		padding: 0.6rem;
	}
	form p {
		grid-column: 1 / -1;
	}
	article {
		padding-block: 1rem;
		border-bottom: 1px solid var(--border, #888);
	}
	code {
		overflow-wrap: anywhere;
	}
</style>
