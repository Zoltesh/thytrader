import { describe, expect, it } from 'vitest';
import {
	allocationBars,
	backtestProblems,
	checkAllocation,
	coefficientText,
	contributionPoints,
	curvePaths,
	drawdownPercent,
	fractionToPercentInput,
	isRevisionConflict,
	jobProgressText,
	journalActorText,
	journalKindLabel,
	largestAssetText,
	multiplyDecimals,
	pairCoefficient,
	parseTab,
	percentInputToFraction,
	pickerOptions,
	portfolioApiError,
	quoteInput,
	remainingWeight,
	roundDecimal,
	shiftDecimal,
	signedPercent,
	sleeveBots,
	weightPercent,
	windowText,
	type Portfolio,
	type PortfolioBacktestJob
} from './portfolios';
import type { Deployment } from './deployments';
import type { StrategyLibraryEntry } from './strategies';

function portfolio(overrides: Partial<Portfolio> = {}): Portfolio {
	return {
		portfolio_id: 'p1',
		name: 'Core',
		mode: 'live',
		quote_currency: 'USDC',
		capital_quote: '300',
		cash_reserve_fraction: '0.17',
		revision: 3,
		created_at: '2026-10-01T00:00:00Z',
		updated_at: '2026-10-01T00:00:00Z',
		limits: {
			max_total_exposure_fraction: '1',
			max_per_asset_fraction: '0.6',
			daily_loss_quote: null,
			max_drawdown_fraction: null
		},
		manager: {
			mandate: '',
			permissions: {
				may_rebalance: false,
				max_weight_change_per_week: '0.1',
				may_pause_sleeves: false,
				may_propose_sleeves: false
			}
		},
		sleeves: [
			{
				sleeve_id: 's1',
				strategy_id: 'st1',
				strategy_name: 'EMA Trend',
				product_id: 'BTC-USDC',
				covered_product_ids: ['BTC-USDC'],
				timeframe: '1h',
				quote_currency: 'USDC',
				strategy_valid: true,
				current_fingerprint: 'sha256:' + 'a'.repeat(64),
				weight_fraction: '0.5',
				capital_quote: '150',
				note: null,
				issues: [],
				created_at: '2026-10-01T00:00:00Z',
				updated_at: '2026-10-01T00:00:00Z'
			}
		],
		allocation: {
			allocated_fraction: '0.5',
			cash_reserve_fraction: '0.17',
			unallocated_fraction: '0.33',
			allocated_quote: '150',
			cash_reserve_quote: '51',
			unallocated_quote: '99',
			assets: [{ asset: 'BTC', weight_fraction: '0.5', sleeve_ids: ['s1'] }],
			largest_asset: { asset: 'BTC', weight_fraction: '0.5', sleeve_ids: ['s1'] },
			largest_asset_within_limit: true
		},
		deployable: false,
		...overrides
	};
}

describe('exact decimal helpers', () => {
	it('shifts and multiplies decimal strings without floats', () => {
		expect(shiftDecimal('0.3333', 2)).toBe('33.33');
		expect(shiftDecimal('50', -2)).toBe('0.5');
		expect(shiftDecimal('0.000', 2)).toBe('0');
		expect(multiplyDecimals('0.3333', '300')).toBe('99.99');
		expect(multiplyDecimals('0.5', '0.1')).toBe('0.05');
	});

	it('converts between percent inputs and fractions', () => {
		expect(percentInputToFraction('33.33')).toBe('0.3333');
		expect(percentInputToFraction('50')).toBe('0.5');
		expect(percentInputToFraction('33.333')).toBeNull();
		expect(percentInputToFraction('-5')).toBeNull();
		expect(fractionToPercentInput('0.0001')).toBe('0.01');
		expect(weightPercent('0.3333')).toBe('33.33%');
	});

	it('accepts positive quote amounts with at most eight places', () => {
		expect(quoteInput('1,000.50')).toBe('1000.5');
		expect(quoteInput('0')).toBeNull();
		expect(quoteInput('1.123456789')).toBeNull();
	});

	it('rounds half away from zero', () => {
		expect(roundDecimal('0.415', 2)).toBe('0.42');
		expect(roundDecimal('-0.415', 2)).toBe('-0.42');
		expect(roundDecimal('0.004', 2)).toBe('0.00');
		expect(coefficientText(null)).toBe('—');
		expect(coefficientText('0.41234')).toBe('0.41');
	});
});

describe('allocation', () => {
	it('checks weights plus reserve against 100% exactly', () => {
		expect(checkAllocation(['0.3333', '0.3333', '0.3334'], '0')).toEqual({
			total: '1',
			remaining: '0',
			ok: true
		});
		const over = checkAllocation(['0.5', '0.5'], '0.0001');
		expect(over.ok).toBe(false);
		expect(over.total).toBe('1.0001');
	});

	it('builds bars for sleeves, reserve, and unallocated cash', () => {
		const bars = allocationBars(portfolio());
		expect(bars.map((bar) => [bar.label, bar.percent, bar.tone])).toEqual([
			['EMA Trend · BTC', '50%', 'sleeve'],
			['Cash reserve', '17%', 'reserve'],
			['Unallocated cash', '33%', 'unallocated']
		]);
		expect(remainingWeight(portfolio())).toBe('0.33');
	});

	it('reports the largest asset against the per-asset limit', () => {
		expect(largestAssetText(portfolio())).toEqual({ text: 'BTC 50% (limit 60%)', over: false });
		const over = portfolio({
			allocation: { ...portfolio().allocation, largest_asset_within_limit: false }
		});
		expect(largestAssetText(over).over).toBe(true);
	});
});

describe('picker and bots', () => {
	const entry = (id: string, product: string | null, valid = true): StrategyLibraryEntry =>
		({
			strategy_id: id,
			name: `Strategy ${id}`,
			product_id: product,
			timeframe: '1h',
			valid
		}) as StrategyLibraryEntry;

	it('disables held, unreadable, and other-quote strategies', () => {
		const options = pickerOptions(
			[
				entry('st1', 'BTC-USDC'),
				entry('st2', 'ETH-USD'),
				entry('st3', null),
				entry('st4', 'SOL-USDC', false)
			],
			portfolio(),
			''
		);
		expect(options.map((option) => option.disabledReason)).toEqual([
			'Already a sleeve',
			'Trades in USD; this portfolio holds USDC',
			'No readable market yet',
			null
		]);
		expect(options[3].warning).toBe('Rules invalid: fix before backtesting');
		expect(pickerOptions([entry('st4', 'SOL-USDC')], portfolio(), 'sol')).toHaveLength(1);
		expect(pickerOptions([entry('st4', 'SOL-USDC')], portfolio(), 'eth')).toHaveLength(0);
	});

	it('lists only active bots of the strategy in the portfolio mode', () => {
		const bot = (id: string, mode: string, status: string): Deployment =>
			({ id, strategy_id: 'st1', mode, status }) as Deployment;
		const inventory = [
			bot('a', 'live', 'running'),
			bot('b', 'paper', 'running'),
			bot('c', 'live', 'stopped')
		];
		expect(sleeveBots(inventory, 'st1', 'live').map((item) => item.id)).toEqual(['a']);
		expect(sleeveBots(null, 'st1', 'live')).toEqual([]);
	});
});

describe('backtest helpers', () => {
	const job = (overrides: Partial<PortfolioBacktestJob>): PortfolioBacktestJob => ({
		job_id: 'j1',
		portfolio_id: 'p1',
		portfolio_revision: 3,
		status: 'running',
		created_at: '',
		updated_at: '',
		expires_at: '',
		evaluation_start: '2026-01-01T00:00:00Z',
		evaluation_end: '2026-01-31T00:00:00Z',
		sleeve_count: 3,
		progress_current: 1,
		progress_total: 4,
		error_message: null,
		failed_detail: null,
		result_fingerprint: null,
		...overrides
	});

	it('describes job progress honestly', () => {
		expect(jobProgressText(job({}))).toBe('Running sleeve 2 of 3…');
		expect(jobProgressText(job({ progress_current: 3 }))).toBe('Combining sleeves…');
		expect(jobProgressText(job({ status: 'queued' }))).toBe('Queued…');
		expect(jobProgressText(job({ status: 'failed', error_message: 'No data' }))).toBe(
			'Failed: No data'
		);
	});

	it('formats returns, drawdowns, and contribution points', () => {
		expect(signedPercent('0.1492')).toBe('+14.92%');
		expect(drawdownPercent('0.048')).toBe('-4.80%');
		expect(drawdownPercent('0')).toBe('0.00%');
		expect(contributionPoints('0.092')).toBe('+9.20 pts');
		expect(windowText('2026-01-01T00:00:00Z', '2026-01-31T00:00:00Z')).toBe(
			'2026-01-01 → 2026-01-31 · 30 days'
		);
	});

	it('finds a correlation pair in either order', () => {
		const correlation = {
			return_clock_seconds: 3600,
			observations: 10,
			pairs: [{ sleeve_ids: ['a', 'b'] as [string, string], coefficient: '0.4' }]
		};
		expect(pairCoefficient(correlation, 'b', 'a')).toBe('0.4');
		expect(pairCoefficient(correlation, 'a', 'c')).toBeNull();
	});

	it('draws both curves on one scale', () => {
		const paths = curvePaths(
			[
				{ at: '2026-01-01T00:00:00Z', equity: '100', basket_equity: '100' },
				{ at: '2026-01-02T00:00:00Z', equity: '110', basket_equity: '90' }
			],
			100,
			50,
			5
		);
		expect(paths?.minAmount).toBe('90');
		expect(paths?.maxAmount).toBe('110');
		expect(paths?.portfolio.startsWith('M0.0 ')).toBe(true);
		expect(curvePaths([], 100, 50, 5)).toBeNull();
	});
});

describe('errors and labels', () => {
	it('reads structured, string, and validation error bodies', () => {
		const conflict = portfolioApiError(409, {
			detail: { code: 'portfolio_revision_conflict', message: 'Changed', current_revision: 4 }
		});
		expect(isRevisionConflict(conflict)).toBe(true);
		expect(portfolioApiError(503, { detail: 'down' }).message).toBe('down');
		expect(
			portfolioApiError(422, { detail: [{ msg: 'Value error, weights must add up' }] }).message
		).toBe('weights must add up');
		const rejected = portfolioApiError(422, {
			detail: {
				code: 'portfolio_backtest_rejected',
				message: 'No data',
				problems: [{ code: 'dataset_missing', message: 'Ingest BTC', sleeve_id: 's1' }]
			}
		});
		expect(backtestProblems(rejected)).toEqual([
			{
				code: 'dataset_missing',
				message: 'Ingest BTC',
				sleeve_id: 's1',
				strategy_id: null,
				strategy_name: null
			}
		]);
	});

	it('labels journal kinds, actors, and tabs', () => {
		expect(journalKindLabel('weights_changed')).toBe('Weights changed');
		expect(journalKindLabel('future_kind')).toBe('future_kind');
		expect(journalActorText({ actor: 'operator', channel: 'browser' })).toBe('You (browser)');
		expect(journalActorText({ actor: 'system', channel: 'system' })).toBe('System');
		expect(parseTab('limits')).toBe('limits');
		expect(parseTab('nope')).toBe('sleeves');
	});
});
