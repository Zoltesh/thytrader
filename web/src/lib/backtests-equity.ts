/**
 * Backtest equity chart model: a time-ordered mark-to-model series with finite
 * chart geometry from exact decimal equity. Re-exported by `backtests.ts`.
 */
import { decimalChartGeometry, utcTimestampSeconds, type HonestLinePoint } from './portfolio';
import type { EquityPoint } from './backtests-types';

export type BacktestEquitySample = {
	time: number;
	amount: string;
	date: string;
	value: number;
};

export type BacktestEquityChartModel = {
	series: HonestLinePoint[];
	samples: BacktestEquitySample[];
	minAmount: string;
	maxAmount: string;
};

const EMPTY_BACKTEST_EQUITY_MODEL: BacktestEquityChartModel = {
	series: [],
	samples: [],
	minAmount: '0',
	maxAmount: '0'
};

export function backtestEquityChartModel(points: readonly EquityPoint[]): BacktestEquityChartModel {
	/** Build a time-ordered mark-to-model series; Y uses finite chart geometry only. */
	const dated: Array<{ date: string; amount: string; time: number }> = [];
	const usedTimes = new Set<number>();
	for (const point of points) {
		const time = utcTimestampSeconds(point.candle_starts_at);
		if (time === null || usedTimes.has(time)) continue;
		usedTimes.add(time);
		dated.push({ date: point.candle_starts_at, amount: point.equity, time });
	}
	if (dated.length < 2) {
		return EMPTY_BACKTEST_EQUITY_MODEL;
	}
	dated.sort((left, right) => left.time - right.time);

	const { values, minAmount, maxAmount } = decimalChartGeometry(dated.map((entry) => entry.amount));
	const samples: BacktestEquitySample[] = dated.map((entry, index) => ({
		time: entry.time,
		amount: entry.amount,
		date: entry.date,
		value: values[index] ?? 0
	}));
	return {
		series: samples.map((sample) => ({ time: sample.time, value: sample.value })),
		samples,
		minAmount,
		maxAmount
	};
}
