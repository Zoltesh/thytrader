import { describe, expect, it } from 'vitest';
import type { CoinbaseCredentialsStatus } from '$lib/credentials';
import type { Deployment, DeploymentPosition } from '$lib/deployments';
import type { HistoryEntry, Portfolio } from '$lib/portfolio';
import type { RiskPolicySnapshot } from '$lib/strategy-workspace';
import type { HistoryRead } from './home-data';
import {
	availableToTradeTile,
	botsTile,
	connectionLine,
	liveExposureTile,
	portfolioValueTile
} from './home-kpis';
import type { Load } from './load';

const NOW = Date.parse('2026-10-02T12:00:00Z');

function ready<T>(data: T): Load<T> {
	return { status: 'ready', data };
}
const loading: Load<never> = { status: 'loading', data: null };
function failed<T>(error: string, data: T | null = null): Load<T> {
	return { status: 'error', data, error };
}

function portfolio(overrides: Partial<Portfolio> = {}): Portfolio {
	return {
		as_of: '2026-10-02T11:59:18Z',
		connection: { provider: 'coinbase', status: 'connected', permissions: ['view', 'trade'] },
		demo: false,
		total_value: { amount: '2418.62', currency: 'USD' },
		assets: [
			{
				currency: 'USDC',
				name: 'USD Coin',
				available: '1240.18',
				hold: '0',
				total: '1240.18',
				value: { amount: '1240.18', currency: 'USD' }
			}
		],
		unvalued_assets: [],
		...overrides
	};
}

function entry(amount: string, asOf: string): HistoryEntry {
	return { as_of: asOf, total_value: { amount, currency: 'USD' } };
}

function history(entries: HistoryEntry[]): Load<HistoryRead> {
	return ready({
		kind: 'ready',
		history: { entries, range: '24h', sampling_interval_seconds: 300 }
	});
}

function deployment(overrides: Partial<Deployment> = {}): Deployment {
	return {
		id: 'dep-1',
		strategy_fingerprint: `sha256:${'a'.repeat(64)}`,
		strategy_id: 'strategy-1',
		kind: 'strategy',
		timeframe: '1h',
		product_id: 'ETH-USDC',
		mode: 'live',
		status: 'running',
		phase: 'flat',
		cash: '100',
		paper_starting_cash: null,
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
		ledger: {
			trade_count: 3,
			total_net_pnl: '0.42',
			total_return_fraction: '0.0042',
			mark_complete: true,
			marked_exposure: '0'
		},
		capital: { allocated_capital: '100.00' },
		...overrides
	};
}

function openPosition(protection: string): DeploymentPosition {
	return {
		product_id: 'ETH-USDC',
		quantity: '0.0381',
		entry_price: '2611.40',
		stop_price: '2560.00',
		target_price: '2648.10',
		entered_bar: '2026-10-01T19:00:00Z',
		side: 'long',
		protection_status: protection
	};
}

const policy: RiskPolicySnapshot = {
	source: 'published',
	version: 7,
	quote_currency: 'USDC',
	allocations: []
};

describe('portfolioValueTile', () => {
	it('names the exact per-currency totals behind the 1:1 USD-pegged value', () => {
		const tile = portfolioValueTile({
			portfolio: ready(
				portfolio({
					total_value_basis: 'usd_pegged_approximate',
					totals: [
						{ amount: '1178.44', currency: 'USD' },
						{ amount: '1240.18', currency: 'USDC' }
					]
				})
			),
			history: ready({ kind: 'unavailable' }),
			nowMs: NOW
		});
		expect(tile).toEqual({
			kind: 'value',
			value: '$2,418.62',
			unit: null,
			lines: [
				{ text: '24h change: — · history is off on this install', tone: 'muted' },
				{ text: '≈ 1,178.44 USD + 1,240.18 USDC, counted 1:1', tone: 'muted' }
			]
		});
	});

	it('shows the newest reading with its 24h change from portfolio history', () => {
		const tile = portfolioValueTile({
			portfolio: ready(portfolio()),
			history: history([
				entry('2410.00', '2026-10-02T11:55:00Z'),
				entry('2376.52', '2026-10-01T12:00:00Z')
			]),
			nowMs: NOW
		});
		expect(tile).toEqual({
			kind: 'value',
			value: '$2,418.62',
			unit: null,
			lines: [{ text: '+$42.10 (+1.77%) · 24h', tone: 'pos', glyph: 'up' }]
		});
	});

	it('uses the newest snapshot while the Coinbase reading is still loading', () => {
		const tile = portfolioValueTile({
			portfolio: loading,
			history: history([
				entry('9800', '2026-10-02T11:58:00Z'),
				entry('10000', '2026-10-02T06:00:00Z')
			]),
			nowMs: NOW
		});
		expect(tile).toMatchObject({
			kind: 'value',
			value: '$9,800.00',
			lines: [{ text: '−$200.00 (−2.00%) · since Oct 2 06:00 UTC', tone: 'neg', glyph: 'down' }]
		});
	});

	it('says why the change is unknown and discloses a stale reading', () => {
		const tile = portfolioValueTile({
			portfolio: ready(portfolio({ as_of: '2026-10-02T11:20:00Z' })),
			history: ready({ kind: 'unavailable' }),
			nowMs: NOW
		});
		expect(tile).toEqual({
			kind: 'value',
			value: '$2,418.62',
			unit: null,
			lines: [
				{ text: '24h change: — · history is off on this install', tone: 'muted' },
				{ text: 'Snapshot 40 min old · refresh for current balances', tone: 'warn', glyph: 'warn' }
			]
		});
	});

	it('shows demo totals as demo data and fresh installs as unknown', () => {
		const demo = portfolio({
			demo: true,
			connection: { provider: 'coinbase', status: 'demo', permissions: [] }
		});
		expect(
			portfolioValueTile({ portfolio: ready(demo), history: loading, nowMs: NOW })
		).toMatchObject({
			kind: 'value',
			value: '$2,418.62',
			lines: [{ text: 'Demo data, not your Coinbase balance', tone: 'warn' }, { tone: 'muted' }]
		});
		expect(
			portfolioValueTile({
				portfolio: ready({ ...demo, assets: [] }),
				history: loading,
				nowMs: NOW
			})
		).toEqual({
			kind: 'unknown',
			reason: 'Connect Coinbase to see your balances.',
			lines: [],
			retryable: false
		});
	});

	it('is loading until a source answers, then unknown with the reason', () => {
		expect(portfolioValueTile({ portfolio: loading, history: loading, nowMs: NOW })).toEqual({
			kind: 'loading'
		});
		expect(
			portfolioValueTile({
				portfolio: failed('Coinbase is temporarily unavailable.'),
				history: ready({ kind: 'unavailable' }),
				nowMs: NOW
			})
		).toMatchObject({
			kind: 'unknown',
			reason: 'Coinbase balances could not be loaded: Coinbase is temporarily unavailable.',
			retryable: true
		});
	});
});

describe('availableToTradeTile', () => {
	it('shows the quote balance and what running live bots reserve', () => {
		const tile = availableToTradeTile({
			portfolio: ready(portfolio()),
			riskPolicy: ready(policy),
			deployments: ready([
				deployment(),
				deployment({ id: 'paused', status: 'paused', capital: { allocated_capital: '50' } }),
				deployment({ id: 'stopped', status: 'stopped' }),
				deployment({ id: 'paper', mode: 'paper', capital: { allocated_capital: '1000' } }),
				deployment({ id: 'usd', product_id: 'BTC-USD' })
			])
		});
		expect(tile).toEqual({
			kind: 'value',
			value: '1,240.18',
			unit: 'USDC',
			lines: [{ text: 'Reserved by live bots: 150.00 USDC', tone: 'muted' }]
		});
	});

	it('discloses partial allocation coverage, holds, and a missing balance', () => {
		const withHold = availableToTradeTile({
			portfolio: ready(
				portfolio({
					assets: [
						{
							currency: 'USDC',
							name: 'USD Coin',
							available: '900',
							hold: '25.5',
							total: '925.5',
							value: { amount: '925.5', currency: 'USD' }
						}
					]
				})
			),
			riskPolicy: ready(policy),
			deployments: ready([deployment(), deployment({ id: 'two', capital: {} })])
		});
		expect(withHold).toMatchObject({
			value: '900.00',
			lines: [
				{ text: 'Reserved by live bots: 100.00 USDC · 1 of 2 bots report it' },
				{ text: '25.50 USDC on hold for open orders' }
			]
		});
		const none = availableToTradeTile({
			portfolio: ready(portfolio({ assets: [] })),
			riskPolicy: ready({ ...policy, quote_currency: 'USDT' }),
			deployments: ready([])
		});
		expect(none).toMatchObject({
			value: '0.00',
			unit: 'USDT',
			lines: [
				{ text: 'Reserved by live bots: none' },
				{ text: 'No USDT balance in the latest snapshot' }
			]
		});
	});

	it('is unknown when the quote currency cannot be read', () => {
		expect(
			availableToTradeTile({
				portfolio: ready(portfolio()),
				riskPolicy: failed('HTTP 503'),
				deployments: ready([])
			})
		).toMatchObject({
			kind: 'unknown',
			reason: 'Quote currency unknown: the risk policy could not be read.',
			retryable: true
		});
	});
});

describe('liveExposureTile', () => {
	it('sums marked live exposure per quote with bot count and protection', () => {
		const tile = liveExposureTile(
			ready([
				deployment({
					positions: [openPosition('covered')],
					ledger: {
						trade_count: 1,
						total_net_pnl: '0',
						total_return_fraction: '0',
						mark_complete: true,
						marked_exposure: '99.50'
					}
				}),
				deployment({ id: 'flat' }),
				deployment({ id: 'paper', mode: 'paper', positions: [openPosition('unprotected')] })
			])
		);
		expect(tile).toEqual({
			kind: 'value',
			value: '99.50',
			unit: 'USDC',
			lines: [{ text: '2 live bots · 1 open · protected', tone: 'muted' }]
		});
	});

	it('warns about unprotected books and says None without live bots', () => {
		const tile = liveExposureTile(
			ready([
				deployment({
					positions: [openPosition('unprotected')],
					ledger: {
						trade_count: 1,
						total_net_pnl: '0',
						total_return_fraction: '0',
						mark_complete: true,
						marked_exposure: '99.50'
					}
				})
			])
		);
		expect(tile).toMatchObject({
			lines: [{ text: '1 live bot · 1 open · 1 unprotected', tone: 'neg', glyph: 'warn' }]
		});
		expect(liveExposureTile(ready([deployment({ mode: 'paper' })]))).toMatchObject({
			value: 'None',
			lines: [{ text: 'No running or paused live bots' }]
		});
	});

	it('is unknown when an open live book has no complete mark', () => {
		expect(
			liveExposureTile(
				ready([
					deployment({
						positions: [openPosition('covered')],
						ledger: {
							trade_count: 0,
							total_net_pnl: null,
							total_return_fraction: null,
							mark_complete: false,
							marked_exposure: null
						}
					})
				])
			)
		).toEqual({
			kind: 'unknown',
			reason: 'An open position has no complete mark, so exposure is not shown.',
			lines: [{ text: '1 live bot · 1 open · protected', tone: 'muted' }],
			retryable: false
		});
	});
});

describe('botsTile', () => {
	it('counts running and paused bots and the bots that need attention', () => {
		const rows = ready([
			deployment(),
			deployment({ id: 'b', mode: 'paper' }),
			deployment({ id: 'c', status: 'paused' }),
			deployment({ id: 'd', status: 'stopped' })
		]);
		expect(botsTile(rows, 1)).toEqual({
			kind: 'value',
			value: '2',
			unit: 'running',
			lines: [{ text: '1 paused · 1 needs attention', tone: 'warn', glyph: 'warn' }]
		});
		expect(botsTile(rows, null)).toMatchObject({
			lines: [{ text: '1 paused · checking what needs attention…', tone: 'muted' }]
		});
		expect(botsTile(failed('HTTP 503'), null)).toMatchObject({ kind: 'unknown', retryable: true });
	});
});

describe('connectionLine', () => {
	const configured: CoinbaseCredentialsStatus = {
		provider: 'coinbase',
		configured: true,
		persisted: true,
		env_file_writable: true,
		api_hot_reloaded: false,
		workers_require_restart: false,
		workers_restart_detail: ''
	};

	it('names the connection, every detected permission, and the snapshot age', () => {
		expect(
			connectionLine({
				portfolio: ready(
					portfolio({
						connection: {
							provider: 'coinbase',
							status: 'connected',
							permissions: ['view', 'trade', 'transfer']
						}
					})
				),
				credentials: ready(configured),
				nowMs: NOW
			})
		).toEqual({
			tone: 'ok',
			text: 'Coinbase connected · View + Trade + Transfer · last snapshot 42s ago'
		});
	});

	it('reports demo mode, checking, and failures honestly', () => {
		expect(
			connectionLine({
				portfolio: ready(portfolio({ demo: true })),
				credentials: loading,
				nowMs: NOW
			}).text
		).toBe('Demo data · no Coinbase credentials configured');
		expect(
			connectionLine({ portfolio: loading, credentials: ready(configured), nowMs: NOW }).text
		).toBe('Coinbase credentials configured · checking the connection…');
		expect(
			connectionLine({
				portfolio: failed('Coinbase is temporarily unavailable.'),
				credentials: loading,
				nowMs: NOW
			})
		).toEqual({
			tone: 'error',
			text: 'Coinbase unavailable · Coinbase is temporarily unavailable.'
		});
		expect(
			connectionLine({
				portfolio: failed('x', portfolio({ as_of: '2026-10-02T11:00:00Z' })),
				credentials: loading,
				nowMs: NOW
			}).text
		).toBe('Coinbase refresh failed · showing the snapshot from 1 h ago');
	});
});
