/**
 * Portfolio chart geometry: allocation bars and equity-curve SVG paths (display
 * floats only; exact values stay strings). Re-exported by `portfolios.ts`.
 */
import { compareDecimalStrings } from './portfolio';
import type { Portfolio, PortfolioEquityPoint } from './portfolios-types';
import { shiftDecimal } from './portfolios-decimal';
import { baseAsset, weightPercent } from './portfolios-format';

export type AllocationBar = {
	key: string;
	label: string;
	fraction: string;
	percent: string;
	/** CSS width (display only). */
	width: string;
	tone: 'sleeve' | 'reserve' | 'unallocated';
};

function cssWidth(fraction: string): string {
	const value = Number(shiftDecimal(fraction, 2));
	return `${Number.isFinite(value) ? Math.max(0, Math.min(100, value)) : 0}%`;
}

/** One bar per sleeve, then the cash reserve, then unallocated cash when any. */
export function allocationBars(portfolio: Portfolio): AllocationBar[] {
	const bars: AllocationBar[] = portfolio.sleeves.map((sleeve) => ({
		key: sleeve.sleeve_id,
		label: `${sleeve.strategy_name}${sleeve.product_id ? ` · ${baseAsset(sleeve.product_id)}` : ''}`,
		fraction: sleeve.weight_fraction,
		percent: weightPercent(sleeve.weight_fraction),
		width: cssWidth(sleeve.weight_fraction),
		tone: 'sleeve'
	}));
	bars.push({
		key: 'reserve',
		label: 'Cash reserve',
		fraction: portfolio.allocation.cash_reserve_fraction,
		percent: weightPercent(portfolio.allocation.cash_reserve_fraction),
		width: cssWidth(portfolio.allocation.cash_reserve_fraction),
		tone: 'reserve'
	});
	if (compareDecimalStrings(portfolio.allocation.unallocated_fraction, '0') > 0) {
		bars.push({
			key: 'unallocated',
			label: 'Unallocated cash',
			fraction: portfolio.allocation.unallocated_fraction,
			percent: weightPercent(portfolio.allocation.unallocated_fraction),
			width: cssWidth(portfolio.allocation.unallocated_fraction),
			tone: 'unallocated'
		});
	}
	return bars;
}

export function allocationAriaLabel(bars: readonly AllocationBar[]): string {
	return `Allocation: ${bars.map((bar) => `${bar.label} ${bar.percent}`).join(', ')}`;
}

export type ChartPaths = {
	portfolio: string;
	basket: string;
	minAmount: string;
	maxAmount: string;
};

function geometryNumber(amount: string): number {
	const value = Number(amount);
	return Number.isFinite(value) ? value : 0;
}

/**
 * SVG paths for the combined and basket curves on one shared scale (display
 * geometry only: x by time, y by value; exact min/max stay strings).
 */
export function curvePaths(
	points: readonly PortfolioEquityPoint[],
	width: number,
	height: number,
	pad: number
): ChartPaths | null {
	if (points.length < 2) return null;
	const amounts = points.flatMap((point) => [point.equity, point.basket_equity]);
	let minAmount = amounts[0];
	let maxAmount = amounts[0];
	for (const amount of amounts) {
		if (compareDecimalStrings(amount, minAmount) < 0) minAmount = amount;
		if (compareDecimalStrings(amount, maxAmount) > 0) maxAmount = amount;
	}
	const minimum = geometryNumber(minAmount);
	const range = geometryNumber(maxAmount) - minimum || 1;
	const first = Date.parse(points[0].at);
	const span = Date.parse(points[points.length - 1].at) - first || 1;
	const path = (pick: (point: PortfolioEquityPoint) => string): string =>
		points
			.map((point, index) => {
				const x = ((Date.parse(point.at) - first) / span) * width;
				const y = pad + (1 - (geometryNumber(pick(point)) - minimum) / range) * (height - pad * 2);
				return `${index === 0 ? 'M' : 'L'}${x.toFixed(1)} ${y.toFixed(1)}`;
			})
			.join(' ');
	return {
		portfolio: path((point) => point.equity),
		basket: path((point) => point.basket_equity),
		minAmount,
		maxAmount
	};
}
