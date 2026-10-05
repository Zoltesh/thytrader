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
	import { readToken } from '$lib/theme';

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

		// Canvas cannot read CSS custom properties, so resolve the design tokens
		// now and again whenever the theme attribute on <html> changes.
		const palette = (): {
			text: string;
			grid: string;
			border: string;
			crosshair: string;
			line: string;
		} => ({
			text: readToken('--faint', '#838e92'),
			grid: readToken('--line', '#20282a'),
			border: readToken('--line-2', '#2b3437'),
			crosshair: readToken('--line-strong', '#3e4a4d'),
			line: readToken('--accent', '#5ce1b5')
		});
		const colors = palette();
		const chart: IChartApi = createChart(el, {
			autoSize: true,
			height,
			layout: {
				attributionLogo: true,
				background: { type: ColorType.Solid, color: 'transparent' },
				fontFamily: readToken('--font-mono', 'ui-monospace, monospace'),
				fontSize: 10,
				textColor: colors.text
			},
			grid: {
				horzLines: { color: colors.grid },
				vertLines: { color: colors.grid }
			},
			rightPriceScale: { borderColor: colors.border },
			timeScale: {
				borderColor: colors.border,
				secondsVisible: false,
				timeVisible: true
			},
			crosshair: {
				mode: CrosshairMode.Magnet,
				horzLine: { color: colors.crosshair, labelVisible: false },
				vertLine: { color: colors.crosshair, labelVisible: true }
			},
			localization: {
				priceFormatter: (price: number) => formatAxisUsd(price)
			}
		});
		const lineOptions = {
			color: colors.line,
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

		const lineSeries = chart.panes().flatMap((pane) => pane.getSeries());
		const themeObserver = new MutationObserver(() => {
			const next = palette();
			chart.applyOptions({
				layout: { textColor: next.text },
				grid: { horzLines: { color: next.grid }, vertLines: { color: next.grid } },
				rightPriceScale: { borderColor: next.border },
				timeScale: { borderColor: next.border },
				crosshair: {
					horzLine: { color: next.crosshair },
					vertLine: { color: next.crosshair }
				}
			});
			for (const item of lineSeries) {
				item.applyOptions({ color: next.line });
			}
		});
		themeObserver.observe(document.documentElement, {
			attributes: true,
			attributeFilter: ['data-theme']
		});

		return () => {
			themeObserver.disconnect();
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
		grid-template-columns: minmax(0, 1fr);
		gap: 8px;
	}
	.chart-host {
		min-width: 0;
		width: 100%;
		height: var(--chart-height, 220px);
		position: relative;
	}
	.crosshair-readout {
		margin: 0;
		color: var(--muted);
		font:
			12px ui-monospace,
			SFMono-Regular,
			Consolas,
			monospace;
	}
</style>
