/**
 * Backtests: typed results API client and display helpers.
 *
 * This module is the public barrel. The code lives in focused modules that never
 * import this barrel:
 * - `backtests-types.ts`: list, summary, result, diagnostics, window, cost-attribution,
 *   benchmark and metrics shapes.
 * - `backtests-diagnostics.ts`: evaluation-window and entry / exit diagnostics text.
 * - `backtests-format.ts`: percents, fingerprints, fees, slippage and spread-stress text.
 * - `backtests-equity.ts`: the equity chart model.
 * - `backtests-api.ts`: list paging helpers and the results readers.
 */
export { compareDecimalStrings } from './portfolio';
export type {
	ApiError,
	BacktestBenchmark,
	BacktestBenchmarkResponse,
	BacktestCostAttribution,
	BacktestDetail,
	BacktestDiagnostics,
	BacktestEvaluationWindow,
	BacktestExitCount,
	BacktestExitReason,
	BacktestFill,
	BacktestList,
	BacktestListQuery,
	BacktestMetricsResponse,
	BacktestPerformanceMetrics,
	BacktestResult,
	BacktestSkipCount,
	BacktestSummary,
	BacktestSummaryEntry,
	BacktestTrade,
	CostAssumptions,
	EquityPoint
} from './backtests-types';
export {
	exitReasonLines,
	formatDiagnosticsFunnel,
	formatEvaluationWindow,
	formatExitReason,
	formatSkipReason,
	unfilledEntryLines
} from './backtests-diagnostics';
export {
	SAME_BAR_POLICY_LABEL,
	formatFillFee,
	formatPercent,
	formatPublishedCosts,
	formatSpreadCostNote,
	optionalSpreadStress,
	shortFingerprint,
	spreadStressBps
} from './backtests-format';
export {
	backtestEquityChartModel,
	type BacktestEquityChartModel,
	type BacktestEquitySample
} from './backtests-equity';
export {
	BACKTEST_LIST_DEFAULT_LIMIT,
	RESULT_FINGERPRINT_PATTERN,
	backtestListPageIsFull,
	fetchAllBacktestsForStrategy,
	fetchBacktest,
	fetchBacktestBenchmark,
	fetchBacktestMetrics,
	fetchBacktests,
	formatBacktestListBound,
	formatListSpreadCue,
	parseResultFingerprintParam
} from './backtests-api';
