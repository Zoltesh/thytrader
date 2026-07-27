<script lang="ts">
	import { chartData, formatUsd, type HistoryEntry } from '$lib/portfolio';

	let {
		entries = [] as HistoryEntry[],
		loading = false
	}: {
		entries?: HistoryEntry[];
		loading?: boolean;
	} = $props();

	// API returns newest-first; chart needs oldest-first for left-to-right time flow.
	const chartEntries = $derived([...entries].reverse());

	const width = 760;
	const height = 220;
	const padding = 40;

	const data = $derived(chartData(chartEntries, width, height, padding));

	const current = $derived(entries.length > 0 ? Number(entries[0].total_value.amount) : 0);
	const high = $derived(data.values.length > 0 ? Math.max(...data.values) : 0);
	const low = $derived(data.values.length > 0 ? Math.min(...data.values) : 0);

	const firstDate = $derived(
		chartEntries.length > 0 ? new Date(chartEntries[0].as_of).toLocaleString() : ''
	);
	const lastDate = $derived(
		chartEntries.length > 0
			? new Date(chartEntries[chartEntries.length - 1].as_of).toLocaleString()
			: ''
	);
</script>

<section class="history-panel" aria-label="Portfolio value history">
	<div class="panel-heading">
		<div>
			<h2>Portfolio history</h2>
			<p>
				{entries.length} saved {entries.length === 1 ? 'snapshot' : 'snapshots'} · sampling every 5 min
			</p>
		</div>
		{#if entries.length >= 2}
			<div class="stats">
				<span class="stat"
					><small>Current</small><strong>{formatUsd(current.toString())}</strong></span
				>
				<span class="stat"><small>High</small><strong>{formatUsd(high.toString())}</strong></span>
				<span class="stat"><small>Low</small><strong>{formatUsd(low.toString())}</strong></span>
			</div>
		{/if}
	</div>

	{#if loading}
		<div class="chart-area">
			<div class="skeleton chart-skeleton"></div>
		</div>
	{:else if entries.length === 0}
		<div class="chart-empty">
			<p>Portfolio history is not configured on this installation.</p>
			<small
				>Run <code>uv run python scripts/setup_local_postgres.py</code> to enable snapshots.</small
			>
		</div>
	{:else if entries.length === 1}
		<div class="chart-empty">
			<p>One snapshot saved.</p>
			<small>History will appear after at least two successful refreshes.</small>
		</div>
	{:else}
		<div class="chart-area">
			<svg
				viewBox="0 0 {width} {height}"
				class="chart"
				role="img"
				aria-label="Portfolio value over time"
			>
				<defs>
					<linearGradient id="area-gradient" x1="0" y1="0" x2="0" y2="1">
						<stop offset="0%" stop-color="#5ce1b5" stop-opacity="0.25" />
						<stop offset="100%" stop-color="#5ce1b5" stop-opacity="0" />
					</linearGradient>
				</defs>

				<!-- Grid lines -->
				{#each [0, 0.25, 0.5, 0.75, 1] as tick (tick)}
					<line
						x1={padding}
						y1={padding + (height - padding * 2) * tick}
						x2={width - padding}
						y2={padding + (height - padding * 2) * tick}
						stroke="#1d2426"
						stroke-width="1"
					/>
				{/each}

				<!-- Area fill -->
				<polygon
					points="{padding},{height - padding} {data.points} {width - padding},{height - padding}"
					fill="url(#area-gradient)"
				/>

				<!-- Line -->
				<polyline points={data.points} fill="none" stroke="#5ce1b5" stroke-width="2.5" />

				<!-- Data points -->
				{#each data.values as value, i (i)}
					<circle
						cx={padding + ((width - padding * 2) * i) / (data.values.length - 1)}
						cy={padding +
							(height - padding * 2) -
							((value - data.min) / (data.max - data.min || 1)) * (height - padding * 2)}
						r="3"
						fill="#5ce1b5"
					/>
				{/each}

				<!-- Axis labels -->
				<text x={padding} y={height - 10} class="axis-label">{firstDate}</text>
				<text x={width - padding} y={height - 10} text-anchor="end" class="axis-label"
					>{lastDate}</text
				>
			</svg>
		</div>
	{/if}
</section>

<style>
	.history-panel {
		border: 1px solid #232b2d;
		background: linear-gradient(145deg, rgba(20, 26, 28, 0.95), rgba(12, 16, 18, 0.95));
		border-radius: 13px;
		overflow: hidden;
		margin-top: 16px;
	}
	.panel-heading {
		display: flex;
		justify-content: space-between;
		align-items: center;
		padding: 22px 24px;
		border-bottom: 1px solid #232b2d;
	}
	h2 {
		margin: 0;
		font-size: 18px;
	}
	.panel-heading p {
		margin: 5px 0 0;
		color: #778386;
		font-size: 12px;
	}
	.stats {
		display: flex;
		gap: 24px;
	}
	.stat {
		display: flex;
		flex-direction: column;
		align-items: flex-end;
	}
	.stat small {
		color: #657174;
		font-size: 10px;
		text-transform: uppercase;
		letter-spacing: 0.06em;
		margin-bottom: 4px;
	}
	.stat strong {
		color: #edf3f3;
		font:
			500 15px ui-monospace,
			SFMono-Regular,
			Consolas,
			monospace;
	}
	.chart-area {
		padding: 20px 24px;
	}
	.chart {
		width: 100%;
		height: auto;
		display: block;
	}
	.chart-empty {
		padding: 40px 24px;
		text-align: center;
	}
	.chart-empty p {
		margin: 0 0 6px;
		color: #849093;
		font-size: 14px;
	}
	.chart-empty small {
		color: #697578;
		font-size: 12px;
	}
	.chart-empty code {
		color: #b8e8d8;
		font-size: 11px;
	}
	.axis-label {
		fill: #657174;
		font:
			10px ui-monospace,
			SFMono-Regular,
			Consolas,
			monospace;
	}
	.chart-skeleton {
		height: 200px;
		border-radius: 8px;
	}
	@media (max-width: 800px) {
		.panel-heading {
			flex-direction: column;
			align-items: flex-start;
			gap: 12px;
		}
		.stats {
			gap: 16px;
		}
	}
</style>
