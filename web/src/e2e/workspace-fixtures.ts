/**
 * Shared mocked-API fixtures for the strategy workspace e2e suites
 * (workspace, research/Test, deploy/Run, backtests redirects, screenshots).
 */
import type { Page, Route } from '@playwright/test';
import { isStrategyLibraryRequest } from './harness';
import { inventoryPageFixture } from './inventory';

export const strategyId = '01985cf0-7b60-7000-8000-000000000003';
export const fingerprint = `sha256:${'a'.repeat(64)}`;
export const fingerprintV2 = `sha256:${'c'.repeat(64)}`;
export const resultFingerprint = `sha256:${'b'.repeat(64)}`;
export const datasetFingerprint = `sha256:${'f'.repeat(64)}`;

/** The strategy's current valid definition (no version or status: ADR 0082). */
export const definition = {
	schema_version: '1.0',
	strategy_id: strategyId,
	name: 'Recovered BTC trend draft',
	description: 'Reference research strategy; not trading authority.',
	created_at: '2026-08-14T12:00:00Z',
	instrument: { product_id: 'BTC-USDC', base_currency: 'BTC', quote_currency: 'USDC' },
	timeframe: '1h',
	data_requirements: {
		warmup_bars: 60,
		required_fields: ['open', 'high', 'low', 'close', 'volume']
	},
	indicators: [
		{ id: 'fast', kind: 'ema', input: 'close', parameters: { period: 21 } },
		{ id: 'slow', kind: 'ema', input: 'close', parameters: { period: 55 } },
		{ id: 'rsi', kind: 'rsi', input: 'close', parameters: { period: 14 } },
		{ id: 'atr_14', kind: 'atr', input: ['high', 'low', 'close'], parameters: { period: 14 } }
	],
	entry: {
		side: 'long',
		when: {
			all: [
				{ left: { indicator: 'fast' }, operator: 'crosses_above', right: { indicator: 'slow' } },
				{ left: { indicator: 'rsi' }, operator: 'less_than', right: { literal: '65' } }
			]
		},
		cooldown_bars: 3,
		max_open_positions: 1
	},
	sizing: {
		kind: 'risk_fraction',
		risk_fraction: '0.01',
		min_quote_notional: '10',
		max_quote_notional: '100'
	},
	portfolio_limits: { max_strategy_exposure_fraction: '0.10', max_concurrent_positions: 1 },
	exits: {
		initial_stop: { kind: 'atr_multiple', atr_indicator: 'atr_14', multiple: '1.5' },
		take_profit: { kind: 'reward_risk', multiple: '2' },
		trailing_stop: { enabled: false },
		time_exit: { max_bars_held: 96 }
	},
	execution: {
		entry_preference: 'maker_only',
		max_entry_wait_bars: 2,
		on_unfilled_entry: 'cancel'
	},
	metadata: { tags: ['reference'], notes: [] }
};

export const backtestSummary = {
	initial_equity: '10000',
	final_equity: '11840',
	total_net_pnl: '1840',
	total_return_fraction: '0.184',
	gross_profit: '2600',
	gross_loss: '760',
	win_rate: '0.46',
	profit_factor: '1.62',
	average_win: null,
	average_loss: null,
	trade_count: 41,
	winning_trade_count: 19,
	maximum_drawdown: '720',
	maximum_drawdown_fraction: '0.072',
	exposure_bars: 400,
	evaluation_bars: 1680,
	validity_limits: ['maker_touch_full_fill', 'stop_before_tp_same_bar']
};

/** An earlier edit of the same strategy (the rules an older run or bot used). */
export const earlierDefinition = {
	...definition,
	indicators: [
		{ id: 'fast', kind: 'ema', input: 'close', parameters: { period: 12 } },
		...definition.indicators.slice(1)
	]
};

export type RecordOverrides = Record<string, unknown>;

/** `GET /api/v1/strategies/{id}` payload for a valid strategy on its current rules. */
export function strategyRecord(overrides: RecordOverrides = {}) {
	return {
		strategy_id: strategyId,
		name: definition.name,
		revision: 1,
		created_at: definition.created_at,
		updated_at: '2026-09-29T12:00:00Z',
		document: definition,
		strategy: definition,
		validation: { valid: true, issues: [] },
		current_fingerprint: fingerprint,
		summary: 'Long when fast EMA crosses above slow EMA and RSI < 65.',
		product_id: 'BTC-USDC',
		timeframe: '1h',
		...overrides
	};
}

/** The same strategy saved as an invalid work in progress. */
export function invalidRecord(overrides: RecordOverrides = {}) {
	return strategyRecord({
		strategy: null,
		current_fingerprint: null,
		validation: {
			valid: false,
			issues: [{ loc: 'entry.when', message: 'unknown indicator references: missing' }]
		},
		...overrides
	});
}

export type StrategyMockOptions = {
	record?: ReturnType<typeof strategyRecord>;
	/** Snapshot definitions by fingerprint; defaults to the current rules and one earlier edit. */
	snapshots?: Record<string, unknown>;
	/** Called for PUT saves; return a response to fulfill with (default: accept at revision+1). */
	onSave?: (body: { document: unknown; revision: number }, route: Route) => Promise<void>;
};

/** `GET/PUT /api/v1/strategies/{id}` plus `GET /api/v1/strategies/snapshots/*`. */
export async function mockStrategy(page: Page, options: StrategyMockOptions = {}): Promise<void> {
	let record = options.record ?? strategyRecord();
	const snapshots = options.snapshots ?? {
		[fingerprint]: definition,
		[fingerprintV2]: earlierDefinition
	};
	await page.route(`**/api/v1/strategies/${strategyId}`, async (route) => {
		const method = route.request().method();
		if (method === 'PUT') {
			const body = route.request().postDataJSON() as { document: unknown; revision: number };
			if (options.onSave) {
				await options.onSave(body, route);
				return;
			}
			record = {
				...record,
				document: body.document as typeof definition,
				revision: record.revision + 1
			};
			await route.fulfill({ json: record });
			return;
		}
		await route.fulfill({ json: record });
	});
	await page.route('**/api/v1/strategies/snapshots/*', (route) => {
		const requested = decodeURIComponent(route.request().url().split('/snapshots/')[1] ?? '');
		const snapshot = snapshots[requested];
		if (snapshot === undefined) {
			return route.fulfill({
				status: 404,
				json: { detail: { code: 'strategy_snapshot_not_found', message: 'unknown fingerprint' } }
			});
		}
		return route.fulfill({
			json: {
				strategy_fingerprint: requested,
				strategy_id: strategyId,
				strategy_name: definition.name,
				strategy: snapshot,
				created_at: '2026-09-20T00:00:00Z',
				is_current: requested === record.current_fingerprint
			}
		});
	});
}

export function backtestEntry(
	strategyFingerprint: string,
	overrides: Record<string, unknown> = {}
) {
	return {
		result_fingerprint: resultFingerprint,
		run_fingerprint: `sha256:${'e'.repeat(64)}`,
		strategy_fingerprint: strategyFingerprint,
		strategy_id: strategyId,
		dataset_fingerprint: datasetFingerprint,
		published_at: '2026-09-29T13:41:00Z',
		summary: backtestSummary,
		...overrides
	};
}

/**
 * GET `/api/v1/backtests?strategy_id=` (workspace) or `?strategy_fingerprint=`
 * (standalone snapshot filter); the callback receives the requested value.
 */
export async function mockBacktestList(
	page: Page,
	entriesFor: (requested: string) => unknown[] = () => []
): Promise<void> {
	await page.route(
		(url) =>
			url.pathname === '/api/v1/backtests' &&
			(url.searchParams.has('strategy_id') || url.searchParams.has('strategy_fingerprint')),
		(route) => {
			const params = new URL(route.request().url()).searchParams;
			const requested = params.get('strategy_id') ?? params.get('strategy_fingerprint') ?? '';
			const entries = entriesFor(requested);
			return route.fulfill({
				json: { entries, limit: 50, offset: 0, returned: entries.length, has_more: false }
			});
		}
	);
}

export async function mockDatasets(page: Page, timeframe = '1h'): Promise<void> {
	await page.route('**/api/v1/market-data/datasets/latest', (route) =>
		route.fulfill({
			json: {
				datasets: [
					{
						product_id: 'BTC-USDC',
						timeframe,
						starts_at: '2025-08-01T00:00:00Z',
						ends_at: '2026-09-28T00:00:00Z',
						content_fingerprint: datasetFingerprint
					}
				]
			}
		})
	);
}

export function suggestedFeeProfile(
	overrides: Record<string, string | null> = {}
): Record<string, string | null> {
	return {
		taker_fee_rate: '0.0040',
		maker_fee_rate: '0.0025',
		usd_volume_30d: '25000.00',
		fee_tier: 'Tier 2 ($10k-$50k)',
		as_of: '2026-09-13T16:00:00Z',
		source: 'coinbase',
		suggested_maker_fee_rate: '0.0025',
		suggested_taker_fee_rate: '0.0040',
		suggestion_source: 'coinbase_account',
		suggestion_unavailable_reason: null,
		suggestion_fee_tier: 'Tier 2 ($10k-$50k)',
		suggestion_schedule_tier_id: 'usd-10k-50k',
		suggestion_schedule_version: 'coinbase-advanced-spot-fees-v1',
		suggestion_schedule_as_of: '2026-09-13',
		schedule_maker_fee_rate: '0.0025',
		schedule_taker_fee_rate: '0.0040',
		suggestion_fetched_at: '2026-09-13T16:00:00Z',
		...overrides
	};
}

export async function mockFees(
	page: Page,
	payload: Record<string, string | null>,
	status = 200
): Promise<void> {
	await page.route('**/api/v1/fees', (route) =>
		status === 200
			? route.fulfill({ json: payload })
			: route.fulfill({
					status,
					json: { detail: { code: 'fees_unavailable', message: 'Fee profile unavailable.' } }
				})
	);
}

/** A deployment payload carrying the full lifecycle contract. */
export function deployment(overrides: Record<string, unknown> = {}) {
	return {
		id: '01985cf0-7b60-7000-8000-000000000222',
		strategy_fingerprint: fingerprint,
		strategy_id: strategyId,
		strategy_name: 'Recovered BTC trend draft',
		strategy_deleted: false,
		kind: 'strategy',
		timeframe: '1h',
		product_id: 'BTC-USDC',
		mode: 'paper',
		status: 'running',
		phase: 'open',
		cash: '9900',
		paper_starting_cash: '10000',
		maker_fee_rate: '0.001',
		taker_fee_rate: '0.002',
		last_evaluated_bar: '2026-09-29T14:00:00Z',
		last_signal: 'not_matched',
		mismatch_detail: null,
		pending_entry_bars: 0,
		bars_held: 3,
		lifecycle_command: 'none',
		daily_loss_latched: false,
		drawdown_latched: false,
		revision: 4,
		worker_lease_held: true,
		created_at: '2026-09-24T09:00:00Z',
		updated_at: '2026-09-29T14:00:05Z',
		position: {
			product_id: 'BTC-USDC',
			quantity: '0.0041',
			entry_price: '63412',
			stop_price: '61902',
			target_price: '66432',
			entered_bar: '2026-09-28T22:00:00Z',
			protection_status: 'protected'
		},
		ledger: {
			trade_count: 6,
			total_net_pnl: '21.40',
			total_return_fraction: '0.0214',
			mark_complete: true,
			marked_exposure: null
		},
		orders: [],
		fills: [],
		...overrides
	};
}

/** GET list of deployments plus optional POST handler. */
export async function mockDeployments(
	page: Page,
	list: () => unknown[],
	onCreate?: (body: unknown, route: Route) => Promise<void>
): Promise<void> {
	await page.route(
		(url) => url.pathname === '/api/v1/deployments',
		async (route) => {
			if (route.request().method() === 'POST' && onCreate) {
				await onCreate(route.request().postDataJSON(), route);
				return;
			}
			await route.fulfill({ json: inventoryPageFixture(list()) });
		}
	);
}

/** Preflight sources with everything reported (credentials, policy, balance, allocation). */
export async function mockPreflight(
	page: Page,
	options: { allocation?: boolean } = {}
): Promise<void> {
	await page.route('**/api/v1/credentials/coinbase', (route) =>
		route.fulfill({
			json: {
				provider: 'coinbase',
				configured: true,
				persisted: true,
				env_file_writable: true,
				api_hot_reloaded: true,
				workers_require_restart: false,
				workers_restart_detail: ''
			}
		})
	);
	await page.route('**/api/v1/risk-policy', (route) =>
		route.fulfill({
			json: {
				source: 'published',
				policy_fingerprint: `sha256:${'7'.repeat(64)}`,
				version: 7,
				quote_currency: 'USDC',
				allocations:
					options.allocation === false ? [] : [{ strategy_id: strategyId, allocated_quote: '100' }]
			}
		})
	);
	await page.route('**/api/v1/portfolio', (route) =>
		route.fulfill({
			json: {
				as_of: '2026-09-29T14:05:00Z',
				connection: { provider: 'coinbase', status: 'connected', permissions: ['view', 'trade'] },
				demo: false,
				total_value: { amount: '1240.18', currency: 'USD' },
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
				unvalued_assets: []
			}
		})
	);
}

export function libraryEntry(overrides: Record<string, unknown> = {}) {
	return {
		strategy_id: strategyId,
		name: 'Recovered BTC trend draft',
		product_id: 'BTC-USDC',
		timeframe: '1h',
		revision: 1,
		valid: true,
		current_fingerprint: fingerprint,
		summary: '',
		backtest: null,
		paper_live: { paper: 'none', live: 'none' },
		active_deployment_count: 0,
		created_at: '2026-08-14T12:00:00Z',
		updated_at: '2026-09-29T12:00:00Z',
		...overrides
	};
}

export async function mockLibrary(page: Page, entries: unknown[]): Promise<void> {
	await page.route(isStrategyLibraryRequest, (route) =>
		route.request().method() === 'GET'
			? route.fulfill({
					json: {
						strategies: entries,
						limit: 100,
						returned: entries.length,
						total: entries.length,
						has_more: false,
						next_cursor: null
					}
				})
			: route.fulfill({ status: 405, json: { detail: 'method not allowed' } })
	);
}

/** Detail, benchmark, and metrics for one result. */
export async function mockBacktestDetail(
	page: Page,
	result: string,
	strategyFingerprint: string
): Promise<void> {
	const curve = Array.from({ length: 60 }, (_, index) => {
		const equity = (10000 + index * 32 + Math.sin(index / 3) * 90).toFixed(2);
		return {
			candle_starts_at: new Date(Date.UTC(2025, 8, 1) + index * 7 * 86_400_000).toISOString(),
			cash: equity,
			base_quantity: '0',
			mark_price: '60000',
			equity
		};
	});
	await page.route(`**/api/v1/backtests/${encodeURIComponent(result)}**`, (route) => {
		const url = route.request().url();
		if (url.endsWith('/benchmark')) {
			return route.fulfill({
				json: {
					result_fingerprint: result,
					benchmark: {
						benchmark_contract_version: 'thytrader-buy-and-hold-v1',
						benchmark_fingerprint: `sha256:${'9'.repeat(64)}`,
						result_fingerprint: result,
						run_fingerprint: `sha256:${'e'.repeat(64)}`,
						dataset_fingerprint: datasetFingerprint,
						engine: 'thytrader-backtest',
						entry_candle_starts_at: '2025-09-01T00:00:00Z',
						exit_candle_starts_at: '2026-09-28T00:00:00Z',
						entry_price: '58000',
						exit_price: '63280',
						initial_equity: '10000',
						final_equity: '10910',
						total_net_pnl: '910',
						total_return_fraction: '0.091',
						total_fees: '12',
						total_spread_cost: null,
						maximum_drawdown: '1900',
						maximum_drawdown_fraction: '0.19',
						evaluation_bars: 1680
					}
				}
			});
		}
		if (url.endsWith('/metrics')) {
			return route.fulfill({
				json: {
					result_fingerprint: result,
					metrics: {
						sharpe: '1.41',
						sortino: '2.02',
						calmar: '2.55',
						cagr: '0.17',
						sqn: '1.9',
						annualized_volatility: '0.12',
						exposure_fraction: '0.24',
						max_consecutive_losses: 4,
						buy_and_hold_return_fraction: '0.091'
					}
				}
			});
		}
		const costs = {
			maker_fee_rate: '0.004',
			taker_fee_rate: '0.006',
			fixed_slippage_bps: '5',
			spread_bps: '0'
		};
		// Mirror the API: the detail route defaults to `detail=summary`, a bounded
		// projection without `result`, trades, or the equity curve.
		if (new URL(url).searchParams.get('detail') !== 'full') {
			return route.fulfill({
				json: {
					result_fingerprint: result,
					run_fingerprint: `sha256:${'e'.repeat(64)}`,
					strategy_fingerprint: strategyFingerprint,
					dataset_fingerprint: datasetFingerprint,
					summary: backtestSummary,
					costs,
					metrics: null
				}
			});
		}
		return route.fulfill({
			json: {
				result_fingerprint: result,
				result: {
					schema_version: '1.0',
					engine: 'thytrader-backtest',
					run_fingerprint: `sha256:${'e'.repeat(64)}`,
					strategy_fingerprint: strategyFingerprint,
					dataset_fingerprint: datasetFingerprint,
					signal_trace_fingerprint: `sha256:${'8'.repeat(64)}`,
					summary: backtestSummary,
					equity_curve: curve,
					trades: []
				},
				costs,
				diagnostics: {
					diagnostics_version: 'thytrader-backtest-diagnostics-v1',
					signals_matched: 40,
					entries_rested: 9,
					entries_filled: 7,
					entries_expired: 2,
					entries_repriced: 0,
					entries_refused_at_fill: 0,
					entries_unfilled_at_end: 0,
					entries_size_capped: 0,
					warmup_bars: 120,
					skipped: [
						{ reason: 'in_position', count: 6 },
						{ reason: 'target_not_positive', count: 25 }
					]
				}
			}
		});
	});
}

/**
 * Retired multi-engine identifiers (ADR 0083). Built from parts so the web/src
 * source scan for engine-variant strings stays clean while tests assert their absence.
 */
export const REMOVED_ENGINE_FIELD = ['engine', 'contract', 'version'].join('_');
export const RETIRED_ENGINE_PREFIX = ['thytrader', 'bar', ''].join('-');
export const RETIRED_ENGINE_ROUTE = ['engine', 'support'].join('-');
