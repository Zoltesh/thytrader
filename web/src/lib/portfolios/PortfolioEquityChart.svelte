<script lang="ts">
	/**
	 * Combined portfolio equity vs the equal-weight basket (inline SVG).
	 *
	 * Both lines share one value scale so the comparison is honest. Geometry is
	 * display-only floating point; the min/max labels are the exact strings.
	 */
	import { curvePaths, quoteText, type PortfolioEquityPoint } from '$lib/portfolios';

	let {
		points,
		currency,
		ariaLabel
	}: {
		points: readonly PortfolioEquityPoint[];
		currency: string;
		ariaLabel: string;
	} = $props();

	const WIDTH = 900;
	const HEIGHT = 180;
	const PAD = 14;

	const paths = $derived(curvePaths(points, WIDTH, HEIGHT, PAD));
	const first = $derived(points[0]?.at.slice(0, 10) ?? '');
	const last = $derived(points.at(-1)?.at.slice(0, 10) ?? '');
</script>

{#if paths === null}
	<p class="empty">Not enough equity points to draw a curve.</p>
{:else}
	<figure class="chart" data-testid="portfolio-equity-chart">
		<div class="legend" aria-hidden="true">
			<span class="key portfolio"><span class="swatch"></span>Portfolio</span>
			<span class="key basket"
				><span class="swatch"></span>Equal-weight basket (buy &amp; hold)</span
			>
			<span class="range"
				>{quoteText(paths.minAmount, currency)} – {quoteText(paths.maxAmount, currency)}</span
			>
		</div>
		<svg
			width="100%"
			height={HEIGHT}
			viewBox="0 0 {WIDTH} {HEIGHT}"
			preserveAspectRatio="none"
			role="img"
			aria-label={ariaLabel}
		>
			<line x1="0" y1={HEIGHT * 0.25} x2={WIDTH} y2={HEIGHT * 0.25} class="grid" />
			<line x1="0" y1={HEIGHT * 0.5} x2={WIDTH} y2={HEIGHT * 0.5} class="grid" />
			<line x1="0" y1={HEIGHT * 0.75} x2={WIDTH} y2={HEIGHT * 0.75} class="grid" />
			<path d={paths.basket} class="line basket" vector-effect="non-scaling-stroke" />
			<path d={paths.portfolio} class="line portfolio" vector-effect="non-scaling-stroke" />
		</svg>
		<figcaption class="axis">
			<span>{first}</span>
			<span>{last}</span>
		</figcaption>
	</figure>
{/if}

<style>
	.chart {
		margin: 0;
	}
	.legend {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 6px 16px;
		margin-bottom: 8px;
		color: var(--muted);
		font-size: var(--fs-sm);
	}
	.key {
		display: inline-flex;
		align-items: center;
		gap: 6px;
	}
	.swatch {
		display: inline-block;
		width: 16px;
		height: 0;
		border-top: 2px solid var(--accent);
	}
	.basket .swatch {
		border-top: 2px dashed var(--faint);
	}
	.range {
		margin-left: auto;
		color: var(--faint);
		font-variant-numeric: tabular-nums;
	}
	svg {
		display: block;
	}
	.grid {
		stroke: var(--line);
		stroke-width: 1;
		vector-effect: non-scaling-stroke;
	}
	.line {
		fill: none;
		stroke-linejoin: round;
	}
	.line.portfolio {
		stroke: var(--accent);
		stroke-width: 2;
	}
	.line.basket {
		stroke: var(--faint);
		stroke-width: 1.5;
		stroke-dasharray: 5 4;
	}
	.axis {
		display: flex;
		justify-content: space-between;
		margin-top: 6px;
		color: var(--faint);
		font-size: var(--fs-xs);
		font-family: var(--font-mono);
	}
	.empty {
		margin: 0;
		color: var(--faint);
	}
</style>
