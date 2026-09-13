<script lang="ts">
	import {
		ColorType,
		CrosshairMode,
		LineSeries,
		createChart,
		type IChartApi,
		type UTCTimestamp
	} from 'lightweight-charts';
	import { formatUsd, honestLineSegments, type HonestLinePoint } from '$lib/portfolio';

	type ChartSample = {
		time: number;
		amount: string;
		date: string;
	};

	let {
		series,
		samples,
		height = 220,
		pointMarkers = false,
		ariaLabel,
		testId,
		hasGaps = false
	}: {
		series: readonly HonestLinePoint[];
		samples: readonly ChartSample[];
		height?: number;
		pointMarkers?: boolean;
		ariaLabel: string;
		testId: string;
		hasGaps?: boolean;
	} = $props();

	let host: HTMLDivElement | undefined = $state();
	let crosshair = $state<ChartSample | null>(null);

	const sampleByTime = $derived.by(() => {
		const lookup: Record<string, ChartSample> = {};
		for (const sample of samples) {
			lookup[String(sample.time)] = sample;
		}
		return lookup;
	});
	const whitespaceCount = $derived(
		series.reduce((count, point) => count + ('value' in point ? 0 : 1), 0)
	);
	const segmentCount = $derived(honestLineSegments(series).length);

	$effect(() => {
		const el = host;
		const points = series;
		const lookup = sampleByTime;
		if (el === undefined || points.length === 0) {
			return;
		}

		const chart: IChartApi = createChart(el, {
			autoSize: true,
			height,
			layout: {
				attributionLogo: true,
				background: { type: ColorType.Solid, color: 'transparent' },
				fontFamily: 'ui-monospace, SFMono-Regular, Consolas, monospace',
				fontSize: 10,
				textColor: '#657174'
			},
			grid: {
				horzLines: { color: '#1d2426' },
				vertLines: { color: '#1d2426' }
			},
			rightPriceScale: { borderColor: '#232b2d' },
			timeScale: {
				borderColor: '#232b2d',
				secondsVisible: false,
				timeVisible: true
			},
			crosshair: {
				mode: CrosshairMode.Magnet,
				horzLine: { color: '#4c5c5e', labelVisible: false },
				vertLine: { color: '#4c5c5e', labelVisible: true }
			},
			localization: {
				priceFormatter: (price: number) => formatAxisUsd(price)
			}
		});
		const lineOptions = {
			color: '#5ce1b5',
			crosshairMarkerVisible: true,
			lastValueVisible: false,
			lineWidth: 2 as const,
			pointMarkersRadius: 3,
			pointMarkersVisible: pointMarkers,
			priceLineVisible: false
		};
		const spacer = chart.addSeries(LineSeries, {
			...lineOptions,
			crosshairMarkerVisible: false,
			lineVisible: false,
			pointMarkersVisible: false
		});
		spacer.setData(points.map((point) => ({ time: point.time as UTCTimestamp })));
		for (const segment of honestLineSegments(points)) {
			const segmentSeries = chart.addSeries(LineSeries, {
				...lineOptions,
				lineVisible: segment.length >= 2,
				pointMarkersVisible: pointMarkers || segment.length === 1
			});
			segmentSeries.setData(
				segment.map((point) => ({ time: point.time as UTCTimestamp, value: point.value }))
			);
		}
		chart.timeScale().fitContent();

		chart.subscribeCrosshairMove((param) => {
			if (typeof param.time !== 'number') {
				crosshair = null;
				return;
			}
			crosshair = lookup[String(param.time)] ?? null;
		});

		return () => {
			crosshair = null;
			chart.remove();
		};
	});

	function formatAxisUsd(price: number): string {
		/** Axis ticks are finite chart geometry, not the stored decimal amount. */
		if (!Number.isFinite(price)) return '';
		const absolute = Math.abs(price);
		const digits = absolute >= 1000 ? 0 : 2;
		return `${price < 0 ? '-$' : '$'}${absolute.toLocaleString('en-US', {
			maximumFractionDigits: digits,
			minimumFractionDigits: digits
		})}`;
	}
</script>

<div class="chart-wrap">
	<div
		bind:this={host}
		class="chart-host"
		style="--chart-height: {height}px"
		data-testid={testId}
		data-has-gaps={hasGaps ? 'true' : 'false'}
		data-sample-count={samples.length}
		data-logical-bar-count={series.length}
		data-whitespace-count={whitespaceCount}
		data-segment-count={segmentCount}
		role="img"
		aria-label={ariaLabel}
	></div>
	<p class="crosshair-readout" aria-live="polite">
		{#if crosshair}
			{formatUsd(crosshair.amount)} · {new Date(crosshair.date).toLocaleString()}
		{:else}
			Move the crosshair onto a stored point for the exact value.
		{/if}
	</p>
</div>

<style>
	.chart-wrap {
		display: grid;
		gap: 8px;
	}
	.chart-host {
		width: 100%;
		height: var(--chart-height, 220px);
		position: relative;
	}
	.crosshair-readout {
		margin: 0;
		color: #8f9d9f;
		font:
			12px ui-monospace,
			SFMono-Regular,
			Consolas,
			monospace;
	}
</style>
