import { describe, expect, it } from 'vitest';
import type { Deployment } from './deployments';
import {
	filterByMode,
	groupDeployments,
	moneyMetricNote,
	moneyMetricText,
	portfolioHeaderMetrics,
	portfolioRow,
	rowIdentity,
	strategyIdentityIndex
} from './deployment-portfolio';
import type { StrategyLibraryEntry } from './strategies';

const fp = (char: string): string => `sha256:${char.repeat(64)}`;

function deployment(overrides: Partial<Deployment> = {}): Deployment {
	return {
		id: 'dep-1',
		strategy_fingerprint: fp('a'),
		strategy_id: 'strategy-1',
		kind: 'strategy',
		timeframe: '1h',
		product_id: 'BTC-USDC',
		mode: 'paper',
		status: 'running',
		phase: 'flat',
		cash: '1000',
		paper_starting_cash: '1000',
		last_evaluated_bar: null,
		last_signal: null,
		mismatch_detail: null,
		pending_entry_bars: 0,
		bars_held: 0,
		lifecycle_command: 'none',
		daily_loss_latched: false,
		drawdown_latched: false,
		revision: 1,
		worker_lease_held: true,
		created_at: '2026-09-24T00:00:00Z',
		updated_at: '2026-09-29T00:00:00Z',
		position: null,
		positions: [],
		orders: [],
		fills: [],
		...overrides
	};
}

const openLong = {
	product_id: 'BTC-USDC',
	quantity: '0.0041',
	entry_price: '63412',
	stop_price: '61902',
	target_price: '66432',
	entered_bar: '2026-09-28T22:00:00Z',
	side: 'long',
	protection_status: 'protected'
};

describe('filter and groups', () => {
	const rows = [
		deployment({ id: 'p-run' }),
		deployment({ id: 'l-run', mode: 'live' }),
		deployment({ id: 'p-paused', status: 'paused' }),
		deployment({ id: 'p-odd', status: 'reconciling' }),
		deployment({ id: 'l-stopped', mode: 'live', status: 'stopped' })
	];

	it('filters by mode and keeps every row for All', () => {
		expect(filterByMode(rows, 'all')).toHaveLength(5);
		expect(filterByMode(rows, 'live').map((row) => row.id)).toEqual(['l-run', 'l-stopped']);
		expect(filterByMode(rows, 'paper').map((row) => row.id)).toEqual([
			'p-run',
			'p-paused',
			'p-odd'
		]);
	});

	it('keeps the existing membership: anything not running/paused/stopped needs attention', () => {
		const groups = groupDeployments(rows);
		expect(groups.attention.map((row) => row.id)).toEqual(['p-odd']);
		expect(groups.running.map((row) => row.id)).toEqual(['p-run', 'l-run']);
		expect(groups.paused.map((row) => row.id)).toEqual(['p-paused']);
		expect(groups.stopped.map((row) => row.id)).toEqual(['l-stopped']);
	});
});

describe('portfolioHeaderMetrics', () => {
	it('counts per group within the filter', () => {
		const metrics = portfolioHeaderMetrics(
			[
				deployment(),
				deployment({ mode: 'live' }),
				deployment({ status: 'paused' }),
				deployment({ status: 'error' })
			],
			'paper'
		);
		expect([metrics.running, metrics.paused, metrics.attention, metrics.stopped]).toEqual([
			1, 1, 1, 0
		]);
	});

	it('never totals paper and live money together', () => {
		const metrics = portfolioHeaderMetrics(
			[
				deployment({ capital: { allocated_capital: '1000' } }),
				deployment({ mode: 'live', capital: { allocated_capital: '100' } })
			],
			'all'
		);
		expect(metrics.allocated.state).toBe('unavailable');
		expect(moneyMetricText(metrics.allocated)).toBe('—');
		expect(moneyMetricNote(metrics.allocated)).toMatch(/never totalled with real money/);
	});

	it('sums exactly per quote currency and reports partial coverage', () => {
		const metrics = portfolioHeaderMetrics(
			[
				deployment({ capital: { allocated_capital: '1000.10', performance_equity: '1021.4' } }),
				deployment({ capital: { allocated_capital: '0.20' } }),
				deployment({ product_id: 'UNI-USD', capital: { allocated_capital: '50' } }),
				deployment({ status: 'stopped', capital: { allocated_capital: '999' } }),
				deployment({})
			],
			'paper'
		);
		expect(moneyMetricText(metrics.allocated)).toBe('50.00 USD · 1,000.30 USDC');
		expect(moneyMetricNote(metrics.allocated)).toBe('3 of 4 bots report it');
		expect(moneyMetricText(metrics.equity)).toBe('1,021.40 USDC');
	});

	it('shows no capital total when no bot has a capital block', () => {
		const metrics = portfolioHeaderMetrics([deployment()], 'all');
		expect(moneyMetricText(metrics.allocated)).toBe('—');
		expect(moneyMetricNote(metrics.allocated)).toBe('No bot reports allocated capital.');
	});

	it('treats flat books as zero exposure and an unmarked open book as unknown', () => {
		const flat = portfolioHeaderMetrics([deployment()], 'paper');
		expect(moneyMetricText(flat.exposure)).toBe('0.00 USDC');
		const marked = portfolioHeaderMetrics(
			[
				deployment(),
				deployment({
					positions: [openLong],
					ledger: {
						trade_count: 1,
						total_net_pnl: '2',
						total_return_fraction: '0.002',
						mark_complete: true,
						marked_exposure: '262.30'
					}
				})
			],
			'paper'
		);
		expect(moneyMetricText(marked.exposure)).toBe('262.30 USDC');
		const unmarked = portfolioHeaderMetrics(
			[
				deployment({
					positions: [openLong],
					ledger: {
						trade_count: 1,
						total_net_pnl: null,
						total_return_fraction: null,
						mark_complete: false,
						marked_exposure: null
					}
				})
			],
			'paper'
		);
		expect(moneyMetricText(unmarked.exposure)).toBe('—');
	});

	it('refuses to total a malformed capital value', () => {
		const metrics = portfolioHeaderMetrics(
			[deployment({ capital: { allocated_capital: 'abc' } })],
			'paper'
		);
		expect(metrics.allocated.state).toBe('unavailable');
	});
});

describe('portfolio rows', () => {
	const library = [
		{
			strategy_id: 'strategy-1',
			name: 'EMA Trend Pullback',
			current_fingerprint: fp('b')
		}
	] as unknown as StrategyLibraryEntry[];
	const index = strategyIdentityIndex(library);

	it('resolves the name by strategy id and says whether the bot runs the current rules', () => {
		expect(rowIdentity(deployment({ strategy_fingerprint: fp('b') }), index)).toEqual({
			name: 'EMA Trend Pullback',
			rules: 'Current rules'
		});
		expect(rowIdentity(deployment({ strategy_fingerprint: fp('a') }), index)).toEqual({
			name: 'EMA Trend Pullback',
			rules: 'Earlier edit'
		});
		expect(
			rowIdentity(
				deployment({ strategy_id: 'unknown', strategy_name: null, strategy_fingerprint: fp('c') }),
				index
			)
		).toEqual({ name: 'Strategy sha256:cccc…cccc', rules: null });
		expect(
			rowIdentity(deployment({ kind: 'discretionary', strategy_fingerprint: null }), index)
		).toEqual({ name: 'Discretionary order', rules: null });
	});

	it('labels a kept live book of a deleted strategy', () => {
		expect(
			rowIdentity(
				deployment({
					mode: 'live',
					strategy_id: null,
					strategy_deleted: true,
					strategy_name: 'Old breakout',
					status: 'stopped'
				}),
				index
			)
		).toEqual({ name: 'Old breakout (deleted strategy)', rules: null });
	});

	it('summarises mode, market with its own quote, position, protection, and PnL', () => {
		const row = portfolioRow(
			deployment({
				mode: 'live',
				positions: [openLong],
				ledger: {
					trade_count: 3,
					total_net_pnl: '0.42',
					total_return_fraction: '0.0042',
					mark_complete: true,
					marked_exposure: null
				}
			}),
			index
		);
		expect(row).toMatchObject({
			modeLabel: 'LIVE',
			market: 'BTC / USDC',
			clock: '1h',
			position: 'Long 0.0041 BTC',
			protection: 'Stop 61902 · TP 66432 · protected',
			pnl: '+0.42 USDC',
			pnlTone: 'pos',
			status: 'Running',
			note: '3 closed trades',
			readOnly: false
		});
	});

	it('reads a resting TP/SL as open and protected, not as exiting (ADR 0097)', () => {
		const resting = portfolioRow(
			deployment({
				phase: 'pending_exit',
				position_state: 'open_protected',
				positions: [{ ...openLong, position_state: 'open_protected', exit_in_flight: false }]
			}),
			index
		);
		expect(resting.protection).toBe('Stop 61902 · TP 66432 · Open · protected (TP/SL resting)');
		const stopOnly = portfolioRow(
			deployment({
				positions: [{ ...openLong, target_price: null, position_state: 'open_protected' }]
			}),
			index
		);
		expect(stopOnly.protection).toBe('Stop 61902 · TP none · Open · protected (stop resting)');
		const exiting = portfolioRow(
			deployment({
				position_state: 'exiting',
				positions: [
					{ ...openLong, position_state: 'exiting', exit_in_flight: true },
					{ ...openLong, product_id: 'ETH-USDC', position_state: 'open_protected' }
				]
			}),
			index
		);
		expect(exiting.protection).toBe('Exiting');
	});

	it('shows — for unknown PnL and flags an incomplete lifecycle contract', () => {
		const partial = deployment({ product_id: 'UNI-USD' });
		delete (partial as Partial<Deployment>).revision;
		const row = portfolioRow(partial, index);
		expect(row.pnl).toBe('—');
		expect(row.market).toBe('UNI / USD');
		expect(row.position).toBe('Flat');
		expect(row.readOnly).toBe(true);
		expect(row.note).toBe('Read-only: lifecycle contract incomplete');
	});

	it('puts a mismatch or latched breaker in the row note first', () => {
		expect(portfolioRow(deployment({ drawdown_latched: true }), index).note).toBe(
			'Drawdown breaker latched'
		);
		expect(portfolioRow(deployment({ mismatch_detail: 'Book drifted' }), index).note).toBe(
			'Book drifted'
		);
	});
});
