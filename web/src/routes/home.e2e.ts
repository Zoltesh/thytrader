import { type Page, type Route } from '@playwright/test';

import { expect, isStrategyLibraryRequest, test } from '../e2e/harness';

/**
 * Home (ADR 0084): header, KPI tiles, chart, Needs attention, Your bots,
 * Holdings, fee tier, and the Data health disclosure. Each source is mocked
 * per test; anything left unmocked reaches the hermetic demo API.
 */

const iso = (msAgo: number): string => new Date(Date.now() - msAgo).toISOString();
const MINUTE = 60_000;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

const demoPortfolio = {
	as_of: '2026-07-27T22:15:00Z',
	connection: {
		provider: 'coinbase',
		status: 'demo',
		permissions: ['view', 'trade', 'transfer']
	},
	demo: true,
	total_value: { amount: '98542.17', currency: 'USD' },
	assets: [
		{
			currency: 'BTC',
			name: 'Bitcoin',
			available: '0.75000000',
			hold: '0.01000000',
			total: '0.76000000',
			value: { amount: '91200.00', currency: 'USD' }
		},
		{
			currency: 'ETH',
			name: 'Ethereum',
			available: '2.25000000',
			hold: '0.00000000',
			total: '2.25000000',
			value: { amount: '7342.17', currency: 'USD' }
		}
	],
	unvalued_assets: []
};

function connectedPortfolio(overrides: Record<string, unknown> = {}): Record<string, unknown> {
	return {
		as_of: iso(30_000),
		connection: {
			provider: 'coinbase',
			status: 'connected',
			permissions: ['view', 'trade', 'transfer']
		},
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
			},
			{
				currency: 'ETH',
				name: 'Ethereum',
				available: '0.4445',
				hold: '0',
				total: '0.4445',
				value: { amount: '1178.44', currency: 'USD' }
			}
		],
		unvalued_assets: [],
		...overrides
	};
}

function generatedAsset(index: number) {
	return {
		currency: `TST${index}`,
		name: `Token ${index}`,
		available: `${index}.00000000`,
		hold: '0.00000000',
		total: `${index}.00000000`,
		value: { amount: String(index * 10), currency: 'USD' }
	};
}

const pagedPortfolio = {
	...demoPortfolio,
	assets: Array.from({ length: 12 }, (_, index) => generatedAsset(index))
};

type HistorySnapshot = { as_of: string; total_value: { amount: string; currency: 'USD' } };

function historyEntry(amount: string, asOf: string): HistorySnapshot {
	return { as_of: asOf, total_value: { amount, currency: 'USD' } };
}

const publishedPolicy = {
	source: 'published',
	version: 7,
	quote_currency: 'USDC',
	allocations: []
};

const configuredCredentials = {
	provider: 'coinbase',
	configured: true,
	persisted: true,
	env_file_writable: true,
	api_hot_reloaded: false,
	workers_require_restart: false,
	workers_restart_detail: ''
};

const feeProfile = {
	taker_fee_rate: '0.0060',
	maker_fee_rate: '0.0040',
	usd_volume_30d: '15250.00',
	fee_tier: 'Tier 1',
	as_of: '2026-08-17T12:00:00Z',
	source: 'coinbase'
};

function catalogReport(datasets: unknown[], warnings: string[] = []): Record<string, unknown> {
	return {
		schema_version: 'thytrader-operator-report-v1',
		application_version: '0.1.0',
		generated_at: iso(0),
		timezone: 'UTC',
		overall_status: warnings.length > 0 ? 'degraded' : 'healthy',
		components: [],
		redaction: {},
		partial_result_warnings: warnings,
		recommended_next_action: 'None.',
		report_kind: 'data_catalog',
		payload: { datasets }
	};
}

function deploymentFixture(overrides: Record<string, unknown> = {}): Record<string, unknown> {
	return {
		id: '01a0ad72-0000-0000-0000-000000000000',
		strategy_fingerprint: `sha256:${'a'.repeat(64)}`,
		strategy_id: '01a0ad42-0000-0000-0000-000000000000',
		strategy_name: 'EMA Trend Pullback',
		kind: 'strategy',
		timeframe: '1h',
		product_id: 'UNI-USD',
		mode: 'paper',
		status: 'running',
		phase: 'flat',
		cash: '100',
		paper_starting_cash: '100',
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
		created_at: '2026-09-20T00:00:00+00:00',
		updated_at: '2026-09-21T00:00:00+00:00',
		position: null,
		positions: [],
		instrument_runtimes: [],
		orders: [],
		fills: [],
		...overrides
	};
}

function openLong(protection: string): Record<string, unknown> {
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

function strategyEntry(overrides: Record<string, unknown> = {}): Record<string, unknown> {
	return {
		strategy_id: '01a0ad42-0000-0000-0000-000000000000',
		name: 'EMA Trend Pullback',
		product_id: 'BTC-USDC',
		timeframe: '1h',
		revision: 3,
		valid: true,
		current_fingerprint: `sha256:${'a'.repeat(64)}`,
		summary: null,
		created_at: '2026-09-01T00:00:00Z',
		updated_at: iso(HOUR),
		backtest: null,
		paper_live: { paper: 'running', live: 'not_deployed' },
		active_deployment_count: 1,
		...overrides
	};
}

function datasetRow(overrides: Record<string, unknown> = {}): Record<string, unknown> {
	return {
		provider: 'coinbase',
		product_id: 'BTC-USDC',
		timeframe: '1h',
		watched: true,
		lookback_hours: 2160,
		worker_status: 'succeeded',
		failure_code: null,
		failure_message: null,
		watch_complete: true,
		complete: true,
		freshness_status: 'fresh',
		covered_starts_at: iso(90 * DAY),
		covered_ends_at: iso(30 * MINUTE),
		expected_candle_count: 2160,
		received_candle_count: 2160,
		gap_count: 0,
		missing_intervals: 0,
		watch_expected_candle_count: 2160,
		watch_status: 'complete',
		history_floor_at: null,
		...overrides
	};
}

type HomeMocks = {
	portfolio?: unknown;
	history?: HistorySnapshot[];
	samplingIntervalSeconds?: number;
	deployments?: unknown[];
	strategies?: unknown[];
	riskPolicy?: unknown;
	credentials?: unknown;
	catalog?: unknown;
	fees?: unknown;
	researchJobs?: Record<string, unknown[]>;
};

/**
 * Route every Home source to a quiet, healthy default. Tests override one
 * source by passing it here, or by registering their own route afterwards
 * (Playwright runs the most recently registered matching route first).
 */
async function mockHome(page: Page, mocks: HomeMocks = {}): Promise<void> {
	await page.route('**/api/v1/portfolio', (route) =>
		route.fulfill({ json: mocks.portfolio ?? connectedPortfolio() })
	);
	await page.route('**/api/v1/portfolio/history**', (route) =>
		route.fulfill({
			json: {
				entries: mocks.history ?? [],
				range: new URL(route.request().url()).searchParams.get('range') ?? '24h',
				sampling_interval_seconds: mocks.samplingIntervalSeconds ?? 300
			}
		})
	);
	await page.route(
		(url) => url.pathname === '/api/v1/deployments',
		(route) => {
			const deployments = mocks.deployments ?? [];
			return route.fulfill({
				json: { deployments, limit: 200, offset: 0, returned: deployments.length }
			});
		}
	);
	await page.route(isStrategyLibraryRequest, (route) =>
		route.fulfill({
			json: { strategies: mocks.strategies ?? [], has_more: false, next_cursor: null }
		})
	);
	await page.route('**/api/v1/risk-policy', (route) =>
		route.fulfill({ json: mocks.riskPolicy ?? publishedPolicy })
	);
	await page.route('**/api/v1/credentials/coinbase', (route) =>
		route.fulfill({ json: mocks.credentials ?? configuredCredentials })
	);
	await page.route('**/api/v1/operator/data-catalog', (route) =>
		route.fulfill({ json: mocks.catalog ?? catalogReport([]) })
	);
	await page.route('**/api/v1/fees', (route) => route.fulfill({ json: mocks.fees ?? feeProfile }));
	await page.route(
		(url) => url.pathname === '/api/v1/research/jobs',
		(route) => {
			const strategyId = new URL(route.request().url()).searchParams.get('strategy_id') ?? '';
			const jobs = mocks.researchJobs?.[strategyId] ?? [];
			return route.fulfill({ json: { jobs, limit: 3, returned: jobs.length } });
		}
	);
}

/** Navigate and wait until the shell (and so Home) has hydrated and attached its handlers. */
async function openHome(page: Page, path = '/'): Promise<void> {
	await page.goto(path);
	await expect(page.locator('[data-shell-hydrated="true"]')).toHaveCount(1);
	await expect(page.getByRole('heading', { name: 'Home', level: 1 })).toBeVisible();
}

const tile = (page: Page, id: string) => page.getByTestId(`kpi-${id}`);
const attention = (page: Page) => page.getByTestId('needs-attention');

async function openDataHealth(page: Page): Promise<void> {
	await page.getByTestId('data-health').locator('summary').click();
	await expect(page.getByRole('region', { name: 'Data-source diagnostics' })).toBeVisible();
}

// ---------------------------------------------------------------- header

test('shows the Home header with connection status, permissions, and primary actions', async ({
	page
}) => {
	await mockHome(page);
	await openHome(page);

	await expect(page).toHaveTitle('Home · ThyTrader');
	const status = page.getByTestId('connection-status');
	await expect(status).toContainText('Coinbase connected · View + Trade + Transfer');
	await expect(status).toContainText(/last snapshot \d+s ago/);
	await expect(page.getByRole('link', { name: 'New order' })).toHaveAttribute('href', '/trade');
	await expect(page.getByRole('link', { name: 'New strategy' })).toHaveAttribute(
		'href',
		'/strategies'
	);
	await expect(page.getByTestId('demo-banner')).toHaveCount(0);
});

test('keeps demo-mode honesty with a banner and demo-labelled figures', async ({ page }) => {
	await mockHome(page, { portfolio: demoPortfolio });
	await openHome(page);

	await expect(page.getByText('Demo data', { exact: true })).toBeVisible();
	await expect(page.getByTestId('demo-banner').getByRole('link')).toHaveAttribute(
		'href',
		'/settings'
	);
	await expect(page.getByTestId('connection-status')).toContainText(
		'Demo data · no Coinbase credentials configured'
	);
	const value = tile(page, 'portfolio-value');
	await expect(value).toContainText('$98,542.17');
	await expect(value).toContainText('Demo data, not your Coinbase balance');
	await expect(value).toContainText('history records live balances only');
	await expect(page.getByTestId('fee-tier')).toContainText('Demo');
});

test('loads the demo portfolio through the real SvelteKit and FastAPI processes', async ({
	page
}) => {
	await openHome(page);

	await expect(page.getByText('Demo data', { exact: true })).toBeVisible();
	await expect(tile(page, 'portfolio-value')).toContainText('$99,792.17');
	// The compiled-default policy quotes USDC and the demo account holds 1250 USDC.
	await expect(tile(page, 'available')).toContainText('1,250.00');
	await expect(tile(page, 'available')).toContainText('USDC');
	await expect(page.getByRole('heading', { name: 'Fee tier' })).toBeVisible();
	await expect(page.getByText('0.60%')).toBeVisible();
	await expect(
		page.getByText(`As of ${new Date('2026-08-17T12:00:00Z').toLocaleString()}`)
	).toBeVisible();
	await expect(page.getByRole('row', { name: /Bitcoin BTC/ })).toBeVisible();
});

test('fresh demo install shows an onboarding path instead of empty zeros', async ({ page }) => {
	await mockHome(page, {
		portfolio: { ...demoPortfolio, assets: [], total_value: { amount: '0', currency: 'USD' } }
	});
	await openHome(page);

	await expect(
		page.getByRole('heading', { name: 'Connect Coinbase to see your portfolio' })
	).toBeVisible();
	await expect(page.getByRole('link', { name: 'Add credentials in Settings' })).toBeVisible();
	await expect(page.getByRole('link', { name: 'Explore Strategies' })).toBeVisible();
	await expect(tile(page, 'portfolio-value-value')).toContainText('—');
	await expect(tile(page, 'portfolio-value')).toContainText(
		'Connect Coinbase to see your balances.'
	);
	await expect(tile(page, 'portfolio-value')).not.toContainText('$0.00');
});

// ---------------------------------------------------------------- KPI tiles

test('computes the KPI tiles from existing endpoints only', async ({ page }) => {
	await mockHome(page, {
		history: [historyEntry('2410.00', iso(5 * MINUTE)), historyEntry('2376.52', iso(DAY - MINUTE))],
		deployments: [
			deploymentFixture({
				id: 'live-flat',
				mode: 'live',
				product_id: 'ETH-USDC',
				capital: { allocated_capital: '100.00' },
				ledger: {
					trade_count: 3,
					total_net_pnl: '0.42',
					total_return_fraction: '0.0042',
					mark_complete: true,
					marked_exposure: '0'
				}
			}),
			deploymentFixture({
				id: 'live-open',
				mode: 'live',
				status: 'paused',
				product_id: 'ETH-USDC',
				positions: [openLong('covered')],
				capital: { allocated_capital: '50' },
				ledger: {
					trade_count: 1,
					total_net_pnl: '0',
					total_return_fraction: '0',
					mark_complete: true,
					marked_exposure: '99.50'
				}
			}),
			deploymentFixture({ id: 'paper-run', product_id: 'BTC-USDC' }),
			deploymentFixture({ id: 'paper-stopped', status: 'stopped' })
		]
	});
	await openHome(page);

	await expect(tile(page, 'portfolio-value-value')).toHaveText('$2,418.62');
	await expect(tile(page, 'portfolio-value')).toContainText('+$42.10 (+1.77%) · 24h');
	await expect(tile(page, 'available-value')).toHaveText(/1,240\.18\s*USDC/);
	await expect(tile(page, 'available')).toContainText('Reserved by live bots: 150.00 USDC');
	await expect(tile(page, 'live-exposure-value')).toHaveText(/99\.50\s*USDC/);
	await expect(tile(page, 'live-exposure')).toContainText('2 live bots · 1 open · protected');
	await expect(tile(page, 'bots-value')).toHaveText(/2\s*running/);
	await expect(tile(page, 'bots')).toContainText('1 paused · 1 needs attention');
});

test('shows — with the reason when a tile cannot be known, and retries its source', async ({
	page
}) => {
	await mockHome(page, {
		deployments: [
			deploymentFixture({
				mode: 'live',
				product_id: 'ETH-USDC',
				positions: [openLong('covered')],
				ledger: {
					trade_count: 0,
					total_net_pnl: null,
					total_return_fraction: null,
					mark_complete: false,
					marked_exposure: null
				}
			})
		]
	});
	let policyReads = 0;
	await page.route('**/api/v1/risk-policy', async (route) => {
		policyReads += 1;
		if (policyReads === 1) {
			await route.fulfill({ status: 503, json: { detail: 'Risk policy storage is unavailable.' } });
			return;
		}
		await route.fulfill({ json: publishedPolicy });
	});
	await openHome(page);

	const available = tile(page, 'available');
	await expect(tile(page, 'available-value')).toContainText('—');
	await expect(available).toContainText(
		'Quote currency unknown: the risk policy could not be read.'
	);
	await expect(tile(page, 'live-exposure-value')).toContainText('—');
	await expect(tile(page, 'live-exposure')).toContainText(
		'An open position has no complete mark, so exposure is not shown.'
	);

	await available.getByRole('button', { name: 'Retry' }).click();
	await expect(tile(page, 'available-value')).toHaveText(/1,240\.18\s*USDC/);
	expect(policyReads).toBe(2);
});

test('shows the stale-snapshot disclosure next to the value', async ({ page }) => {
	await mockHome(page, { portfolio: connectedPortfolio({ as_of: '2026-07-27T22:15:00Z' }) });
	await openHome(page);

	await expect(tile(page, 'portfolio-value')).toContainText(
		/Snapshot \d+ days old · refresh for current balances/
	);
	await expect(page.getByTestId('holdings')).toContainText(/Snapshot \d+ days old/);
});

test('refuses to state a 24h trend from a single history sample', async ({ page }) => {
	await mockHome(page, { history: [historyEntry('10450.50', iso(2 * MINUTE))] });
	// The Coinbase reading never arrives; the tile uses the newest snapshot.
	await page.route('**/api/v1/portfolio', () => new Promise<void>(() => undefined));
	await openHome(page);

	await expect(tile(page, 'portfolio-value-value')).toHaveText('$10,450.50');
	await expect(tile(page, 'portfolio-value')).toContainText(
		'24h change: — · needs two snapshots in 24h'
	);
});

// ---------------------------------------------------------------- chart

async function openWithHistory(
	page: Page,
	history: { status: number } | { entries: HistorySnapshot[]; sampling_interval_seconds?: number }
): Promise<void> {
	await mockHome(page);
	await page.route('**/api/v1/portfolio/history**', async (route) => {
		if ('status' in history) {
			await route.fulfill({
				status: history.status,
				json: { detail: { code: 'persistence_unavailable', message: 'unavailable' } }
			});
			return;
		}
		await route.fulfill({
			json: {
				entries: history.entries,
				range: '7d',
				sampling_interval_seconds: history.sampling_interval_seconds ?? 300
			}
		});
	});
	await openHome(page);
	await expect(page.getByRole('heading', { name: 'Portfolio value', level: 2 })).toBeVisible();
}

test('requests each chart range from the existing history API', async ({ page }) => {
	const ranges: string[] = [];
	await mockHome(page);
	await page.route('**/api/v1/portfolio/history**', async (route) => {
		const range = new URL(route.request().url()).searchParams.get('range') ?? '';
		ranges.push(range);
		await route.fulfill({
			json: {
				entries: [
					historyEntry('120', iso(10 * MINUTE)),
					historyEntry('110', iso(20 * DAY)),
					historyEntry('100', iso(120 * DAY))
				],
				range,
				sampling_interval_seconds: 3600
			}
		});
	});
	await openHome(page);

	const pills = page.getByRole('group', { name: 'Chart range' });
	await expect(pills.getByRole('button', { name: '1W' })).toHaveAttribute('aria-pressed', 'true');
	await expect.poll(() => ranges).toEqual(expect.arrayContaining(['24h', '7d']));

	await pills.getByRole('button', { name: '1M' }).click();
	await expect(pills.getByRole('button', { name: '1M' })).toHaveAttribute('aria-pressed', 'true');
	await expect.poll(() => ranges.at(-1)).toBe('30d');

	await pills.getByRole('button', { name: '1D' }).click();
	await expect.poll(() => ranges.at(-1)).toBe('24h');

	// 3M has no server range: it reads `all` and keeps the last 90 days.
	await pills.getByRole('button', { name: '3M' }).click();
	await expect.poll(() => ranges.at(-1)).toBe('all');
	const meta = page.getByTestId('chart-meta');
	await expect(meta).toContainText('2 sampled snapshots');
	await expect(meta).toContainText('last 90 days of the all-time history');
});

test('labels history sampling as a target interval rather than a guaranteed cadence', async ({
	page
}) => {
	await openWithHistory(page, {
		entries: [
			historyEntry('120', '2026-07-27T12:00:00Z'),
			historyEntry('110', '2026-07-27T11:00:00Z'),
			historyEntry('100', '2026-07-27T10:00:00Z')
		],
		sampling_interval_seconds: 3600
	});

	const chart = page.getByTestId('portfolio-chart');
	await expect(chart).toContainText('3 sampled snapshots');
	await expect(chart).toContainText('target interval 1h');
	await expect(chart).not.toContainText('every');
	await expect(chart).not.toContainText('representative sample');
});

test('shows empty history copy and does not invent snapshots on refresh', async ({ page }) => {
	await openWithHistory(page, { entries: [] });

	await expect(page.getByText('No snapshots exist in this range yet.')).toBeVisible();
	await expect(
		page.getByText(
			'The worker records live portfolios automatically; Refresh never creates chart points.'
		)
	).toBeVisible();

	await page.getByRole('button', { name: 'Refresh balances' }).click();

	await expect(page.getByText('No snapshots exist in this range yet.')).toBeVisible();
	await expect(page.getByTestId('portfolio-history-chart')).toHaveCount(0);
});

test('shows single-snapshot history copy until a line can be drawn', async ({ page }) => {
	await openWithHistory(page, { entries: [historyEntry('100', '2026-07-27T10:00:00Z')] });

	await expect(page.getByText('One snapshot is available.')).toBeVisible();
	await expect(
		page.getByText('A line appears after the next successful scheduled observation.')
	).toBeVisible();
	await expect(page.getByTestId('portfolio-history-chart')).toHaveCount(0);
});

test('shows unavailable history copy when persistence is disabled', async ({ page }) => {
	await openWithHistory(page, { status: 503 });

	await expect(
		page.getByText('Portfolio history is unavailable on this installation.')
	).toBeVisible();
	await expect(
		page.getByText('Start the full local stack to enable durable scheduled snapshots.')
	).toBeVisible();
	await expect(tile(page, 'portfolio-value')).toContainText(
		'24h change: — · history is off on this install'
	);
});

test('shows failed history copy with its own retry when the history API errors', async ({
	page
}) => {
	let reads = 0;
	await mockHome(page);
	await page.route('**/api/v1/portfolio/history**', async (route) => {
		const range = new URL(route.request().url()).searchParams.get('range');
		if (range === '7d') reads += 1;
		if (range === '7d' && reads === 1) {
			await route.fulfill({ status: 502, json: { detail: 'Bad gateway' } });
			return;
		}
		await route.fulfill({
			json: {
				entries: [
					historyEntry('120', '2026-07-27T12:00:00Z'),
					historyEntry('110', '2026-07-27T11:00:00Z')
				],
				range,
				sampling_interval_seconds: 3600
			}
		});
	});
	await openHome(page);

	const chart = page.getByTestId('portfolio-chart');
	await expect(chart).toContainText('Portfolio history could not be loaded.');
	await expect(chart).toContainText('Try again after the API and worker report healthy.');
	await chart.getByRole('button', { name: 'Retry' }).click();
	await expect(page.getByTestId('portfolio-history-chart')).toBeVisible();
	expect(reads).toBe(2);
});

test('places history points by wall-clock time and notes an orphan post-gap snapshot', async ({
	page
}) => {
	await openWithHistory(page, {
		entries: [
			historyEntry('120', '2026-07-27T11:00:00Z'),
			historyEntry('110', '2026-07-27T10:05:00Z'),
			historyEntry('100', '2026-07-27T10:00:00Z')
		]
	});

	const chart = page.getByTestId('portfolio-history-chart');
	await expect(chart).toBeVisible();
	await expect(chart.locator('canvas').first()).toBeVisible();
	await expect(chart).toHaveAttribute('data-sample-count', '3');
	await expect(chart).toHaveAttribute('data-has-gaps', 'true');
	await expect(chart).toHaveAttribute('data-segment-count', '2');
	const logicalBars = Number(await chart.getAttribute('data-logical-bar-count'));
	const whitespace = Number(await chart.getAttribute('data-whitespace-count'));
	expect(logicalBars).toBeGreaterThan(3);
	expect(whitespace).toBeGreaterThan(0);
	await expect(
		page.getByText(
			'Gaps indicate missed worker observations; the line is intentionally not interpolated.'
		)
	).toBeVisible();
});

test('does not show a gap note for contiguous snapshots', async ({ page }) => {
	await openWithHistory(page, {
		entries: [
			historyEntry('120', '2026-07-27T12:00:00Z'),
			historyEntry('110', '2026-07-27T11:00:00Z'),
			historyEntry('100', '2026-07-27T10:00:00Z')
		],
		sampling_interval_seconds: 3600
	});

	const chart = page.getByTestId('portfolio-history-chart');
	await expect(chart).toBeVisible();
	await expect(chart.locator('canvas').first()).toBeVisible();
	await expect(chart).toHaveAttribute('data-has-gaps', 'false');
	await expect(chart).toHaveAttribute('data-segment-count', '1');
	await expect(chart).toHaveAttribute('data-sample-count', '3');
	await expect(chart).toHaveAttribute('data-logical-bar-count', '3');
	await expect(
		page.getByText(
			'Gaps indicate missed worker observations; the line is intentionally not interpolated.'
		)
	).toHaveCount(0);
});

function thinnedWeek(outageHours: number): HistorySnapshot[] {
	// The API buckets a long range into 300 representative snapshots.
	const step = 2016 * 1000;
	return Array.from({ length: 300 }, (_, index) =>
		historyEntry(
			String(2400 - index),
			iso(index * step + (index >= 150 ? outageHours * HOUR : 0) + MINUTE)
		)
	);
}

test('treats a thinned 300-snapshot range as representative, not as 299 gaps', async ({ page }) => {
	await openWithHistory(page, { entries: thinnedWeek(0), sampling_interval_seconds: 300 });

	const chart = page.getByTestId('portfolio-history-chart');
	await expect(chart).toHaveAttribute('data-sample-count', '300');
	await expect(chart).toHaveAttribute('data-has-gaps', 'false');
	await expect(chart).toHaveAttribute('data-segment-count', '1');
	await expect(page.getByTestId('chart-meta')).toContainText('representative sample');
});

test('keeps a real outage visible inside a thinned range', async ({ page }) => {
	await openWithHistory(page, { entries: thinnedWeek(6), sampling_interval_seconds: 300 });

	const chart = page.getByTestId('portfolio-history-chart');
	await expect(chart).toHaveAttribute('data-has-gaps', 'true');
	await expect(chart).toHaveAttribute('data-segment-count', '2');
});

// ---------------------------------------------------------------- needs attention

test('lists bot problems with an icon, words, a LIVE tag, and a Review link to the bot', async ({
	page
}) => {
	await mockHome(page, {
		deployments: [
			deploymentFixture({
				id: 'paused-paper',
				strategy_id: null,
				strategy_name: 'UNI Momentum',
				product_id: 'UNI-USDC',
				timeframe: '15m',
				status: 'paused',
				mismatch_detail: 'User-order feed was stale for 2m.'
			}),
			deploymentFixture({
				id: 'latched-live',
				strategy_id: null,
				strategy_name: 'RSI Reversion',
				mode: 'live',
				product_id: 'ETH-USDC',
				daily_loss_latched: true
			}),
			deploymentFixture({
				id: 'bare-live',
				strategy_id: null,
				strategy_name: 'SOL Breakout',
				mode: 'live',
				product_id: 'SOL-USDC',
				positions: [{ ...openLong('unprotected'), product_id: 'SOL-USDC' }]
			}),
			deploymentFixture({ id: 'healthy', strategy_id: null, strategy_name: 'Quiet Bot' })
		]
	});
	await openHome(page);

	const items = attention(page).getByTestId('attention-item');
	await expect(items).toHaveCount(3);
	await expect(page.getByTestId('attention-count')).toHaveText('3');
	// Critical real-money items first, then by headline.
	await expect(items.nth(0)).toContainText('RSI Reversion: daily-loss breaker tripped');
	await expect(items.nth(0)).toContainText('risk-increasing entries stay blocked');
	await expect(items.nth(0)).toHaveAttribute('data-severity', 'critical');
	await expect(items.nth(0)).toContainText('LIVE');
	await expect(items.nth(1)).toContainText('SOL Breakout has a live position without exit cover');
	await expect(items.nth(1)).toHaveAttribute('data-severity', 'critical');
	await expect(items.nth(2)).toContainText('UNI Momentum paused');
	await expect(items.nth(2)).toHaveAttribute('data-severity', 'warning');
	await expect(items.nth(2)).toContainText(
		'Paper · UNI / USDC · 15m · User-order feed was stale for 2m.'
	);
	await expect(items.nth(2)).not.toContainText('LIVE');
	await expect(
		items.nth(2).getByRole('link', { name: 'Review: UNI Momentum paused' })
	).toHaveAttribute('href', '/deployments/paused-paper');
	// Icons are decorative; the words carry the meaning, and severity is read out.
	await expect(items.nth(0).locator('span.icon')).toHaveAttribute('aria-hidden', 'true');
	await expect(items.nth(0)).toContainText('Critical:');
	await expect(items.nth(2)).toContainText('Needs attention:');
	await expect(tile(page, 'bots')).toContainText('3 need attention');
});

test('flags missing credentials and an unpublished risk policy while live bots exist', async ({
	page
}) => {
	await mockHome(page, {
		deployments: [deploymentFixture({ mode: 'live', product_id: 'ETH-USDC' })],
		credentials: { ...configuredCredentials, configured: false },
		riskPolicy: { ...publishedPolicy, source: 'compiled_default', version: 1 }
	});
	await openHome(page);

	const items = attention(page).getByTestId('attention-item');
	await expect(items).toHaveCount(2);
	await expect(items.nth(0)).toContainText('No Coinbase credentials for live bots');
	await expect(items.nth(0).getByRole('link')).toHaveAttribute('href', '/settings');
	await expect(items.nth(1)).toContainText('No published risk policy');
	await expect(items.nth(1)).toContainText('thytrader-runtime set-risk-policy --confirm');
	await expect(items.nth(1).getByRole('link')).toHaveAttribute('href', '/chat');
});

test('does not raise setup items when no live bot runs', async ({ page }) => {
	await mockHome(page, {
		deployments: [deploymentFixture({ mode: 'paper' })],
		credentials: { ...configuredCredentials, configured: false },
		riskPolicy: { ...publishedPolicy, source: 'compiled_default' }
	});
	await openHome(page);

	await expect(page.getByTestId('attention-empty')).toContainText('Nothing needs you right now');
	await expect(attention(page).getByTestId('attention-item')).toHaveCount(0);
});

test('reports watched datasets that failed, are stuck backfilling, stale, or gapped', async ({
	page
}) => {
	await mockHome(page, {
		strategies: [
			strategyEntry({
				strategy_id: 'sol-strategy',
				name: 'SOL Breakout',
				product_id: 'SOL-USDC',
				timeframe: '5m'
			})
		],
		catalog: catalogReport([
			datasetRow({ product_id: 'BTC-USDC', timeframe: '1h' }),
			datasetRow({
				product_id: 'ETH-USDC',
				timeframe: '1h',
				worker_status: 'failed',
				failure_code: 'provider_unavailable',
				failure_message: 'Historical market-data retrieval failed.'
			}),
			datasetRow({
				product_id: 'ATOM-USDC',
				timeframe: '4h',
				watch_complete: false,
				watch_status: 'backfilling',
				received_candle_count: 720,
				watch_expected_candle_count: 2190
			}),
			datasetRow({ product_id: 'UNI-USDC', timeframe: '15m', freshness_status: 'stale' }),
			datasetRow({ product_id: 'SOL-USDC', timeframe: '5m', gap_count: 1, missing_intervals: 3 }),
			datasetRow({
				product_id: 'DOGE-USDC',
				timeframe: '1h',
				watched: false,
				freshness_status: 'stale'
			})
		])
	});
	const ingestionReads: string[] = [];
	await page.route('**/api/v1/market-data/ingestion?**', async (route) => {
		const url = new URL(route.request().url());
		ingestionReads.push(
			`${url.searchParams.get('product_id')}|${url.searchParams.get('timeframe') ?? ''}`
		);
		await route.fulfill({
			json: {
				product_id: 'ATOM-USDC',
				timeframe: '4h',
				status: 'succeeded',
				last_attempt_at: iso(3 * HOUR),
				last_success_at: iso(3 * HOUR),
				next_attempt_at: iso(3 * HOUR - 5 * MINUTE),
				watch_complete: false,
				failure: null
			}
		});
	});
	await openHome(page);

	const items = attention(page).getByTestId('attention-item');
	await expect(items).toHaveCount(4);
	// Only the backfilling watched dataset needs its ingestion schedule checked.
	expect(ingestionReads).toEqual(['ATOM-USDC|4h']);
	await expect(attention(page)).toContainText('ETH-USDC 1h ingest failed');
	await expect(attention(page)).toContainText('Historical market-data retrieval failed.');
	await expect(attention(page)).toContainText('ATOM-USDC 4h backfill is stuck');
	await expect(attention(page)).toContainText('720 of 2190 candles');
	await expect(attention(page)).toContainText('UNI-USDC 15m data is stale');
	await expect(attention(page)).not.toContainText('DOGE-USDC');
	const gaps = items.filter({ hasText: 'SOL-USDC 5m has 3 missing bars' });
	await expect(gaps).toContainText('used by SOL Breakout');
	await expect(gaps.getByRole('link', { name: /^Open strategy/ })).toHaveAttribute(
		'href',
		'/strategies/sol-strategy/test'
	);

	// Datasets without a strategy link to Data health, which opens in place.
	const health = page.getByTestId('data-health');
	await expect(health).not.toHaveAttribute('open', '');
	await items
		.filter({ hasText: 'ATOM-USDC 4h backfill is stuck' })
		.getByRole('link', { name: /^Data health/ })
		.click();
	await expect(health).toHaveAttribute('open', '');
	await expect(page.getByTestId('data-health-summary')).toHaveText(
		'5 watched datasets · 4 need attention'
	);
	const rows = health.getByTestId('watched-dataset');
	await expect(rows).toHaveCount(5);
	await expect(rows.filter({ hasText: 'ATOM-USDC' })).toContainText('Backfill stuck');
	await expect(rows.filter({ hasText: 'BTC-USDC' })).toContainText('OK');
});

test('discloses a partial data catalog instead of claiming all clear', async ({ page }) => {
	await mockHome(page, {
		catalog: catalogReport([], ['Watchlist is unavailable; catalog omits watch flags.'])
	});
	await openHome(page);

	await expect(page.getByTestId('attention-sources')).toContainText(
		'Watched datasets: Watchlist is unavailable; catalog omits watch flags.'
	);
	await expect(page.getByTestId('attention-empty')).toContainText('Nothing needs you right now');
});

test('surfaces a failed research job with a link to the strategy Test stage', async ({ page }) => {
	await mockHome(page, {
		strategies: [strategyEntry({ strategy_id: 'ema', name: 'EMA Trend Pullback' })],
		researchJobs: {
			ema: [
				{
					job_id: 'job-1',
					kind: 'backtest',
					status: 'failed',
					created_at: iso(2 * HOUR),
					updated_at: iso(2 * HOUR - MINUTE),
					error_message: 'Dataset verification failed.',
					failed_phase: 'dataset_resolution',
					failed_detail: null,
					strategy_id: 'ema'
				}
			]
		}
	});
	await openHome(page);

	const item = attention(page).getByTestId('attention-item');
	await expect(item).toHaveCount(1);
	await expect(item).toContainText('Backtest failed · EMA Trend Pullback');
	await expect(item).toContainText(
		'Failed during dataset resolution: Dataset verification failed.'
	);
	await expect(item.getByRole('link', { name: /^Open Test/ })).toHaveAttribute(
		'href',
		'/strategies/ema/test'
	);
});

test('lists a failed source with its own retry instead of claiming all clear', async ({ page }) => {
	await mockHome(page, { strategies: [strategyEntry({ strategy_id: 'ema' })] });
	let jobReads = 0;
	await page.route(
		(url) => url.pathname === '/api/v1/research/jobs',
		async (route) => {
			jobReads += 1;
			if (jobReads === 1) {
				await route.fulfill({
					status: 503,
					json: {
						detail: { code: 'research_jobs_unavailable', message: 'Research jobs are unavailable.' }
					}
				});
				return;
			}
			await route.fulfill({ json: { jobs: [], limit: 3, returned: 0 } });
		}
	);
	await openHome(page);

	const sources = page.getByTestId('attention-sources');
	await expect(sources).toContainText(
		"Couldn't check research jobs: Research jobs are unavailable."
	);
	await expect(page.getByTestId('attention-empty')).toContainText(
		'Nothing found in the sources that loaded.'
	);
	await sources.getByRole('button', { name: 'Retry' }).click();
	await expect(page.getByTestId('attention-empty')).toContainText('Nothing needs you right now');
	await expect(page.getByTestId('attention-sources')).toHaveCount(0);
});

test('keeps Home usable while the data catalog is slow', async ({ page }) => {
	await mockHome(page, {
		deployments: [deploymentFixture({ id: 'paper-run', product_id: 'BTC-USDC' })]
	});
	let releaseCatalog: () => void = () => undefined;
	const catalogGate = new Promise<void>((resolve) => {
		releaseCatalog = resolve;
	});
	await page.route('**/api/v1/operator/data-catalog', async (route: Route) => {
		await catalogGate;
		await route.fulfill({
			json: catalogReport([datasetRow({ product_id: 'UNI-USDC', freshness_status: 'stale' })])
		});
	});
	await openHome(page);

	// Every other card renders while the catalog is still loading.
	await expect(tile(page, 'portfolio-value-value')).toHaveText('$2,418.62');
	await expect(tile(page, 'bots-value')).toHaveText(/1\s*running/);
	await expect(page.getByTestId('home-bot-row')).toHaveCount(1);
	await expect(page.getByTestId('holdings').getByTestId('holding-row')).toHaveCount(2);
	await expect(page.getByTestId('attention-sources')).toContainText('Checking watched datasets…');
	await expect(page.getByTestId('data-health-summary')).toHaveText('reading the data catalog…');

	releaseCatalog();
	await expect(attention(page)).toContainText('UNI-USDC 1h data is stale');
	await expect(page.getByTestId('attention-sources')).toHaveCount(0);
});

// ---------------------------------------------------------------- your bots

test('lists running and paused bots with mode chips, markets, PnL, and links', async ({ page }) => {
	await mockHome(page, {
		deployments: [
			deploymentFixture({
				id: 'paper-paused',
				strategy_id: null,
				strategy_name: 'UNI Momentum',
				product_id: 'UNI-USDC',
				timeframe: '15m',
				status: 'paused',
				ledger: {
					trade_count: 4,
					total_net_pnl: '-4.00',
					total_return_fraction: '-0.008',
					mark_complete: true,
					marked_exposure: '0'
				}
			}),
			deploymentFixture({
				id: 'live-run',
				strategy_id: null,
				strategy_name: 'RSI Reversion',
				mode: 'live',
				product_id: 'ETH-USDC',
				ledger: {
					trade_count: 3,
					total_net_pnl: '0.42',
					total_return_fraction: '0.0042',
					mark_complete: true,
					marked_exposure: '0'
				}
			}),
			deploymentFixture({ id: 'old', strategy_id: null, strategy_name: 'Old', status: 'stopped' })
		]
	});
	await openHome(page);

	const card = page.getByTestId('your-bots');
	const rows = card.getByTestId('home-bot-row');
	await expect(rows).toHaveCount(2);
	await expect(rows.nth(0)).toHaveAttribute('data-mode', 'live');
	await expect(rows.nth(0)).toContainText('RSI Reversion');
	await expect(rows.nth(0)).toContainText('LIVE');
	await expect(rows.nth(0)).toContainText('ETH / USDC · 1h');
	await expect(rows.nth(0)).toContainText('+0.42 USDC');
	await expect(rows.nth(0).getByRole('link')).toHaveAttribute('href', '/deployments/live-run');
	await expect(rows.nth(1)).toContainText('Paper');
	await expect(rows.nth(1)).toContainText('UNI / USDC · 15m');
	await expect(rows.nth(1)).toContainText('-4.00 USDC');
	await expect(rows.nth(1)).toContainText('Paused');
	await expect(card).not.toContainText('Old');
	await expect(card.getByRole('link', { name: 'Portfolio →' })).toHaveAttribute(
		'href',
		'/deployments'
	);
});

test('shows a bots error with its own retry while the rest of Home loads', async ({ page }) => {
	await mockHome(page);
	let reads = 0;
	await page.route(
		(url) => url.pathname === '/api/v1/deployments',
		async (route) => {
			reads += 1;
			if (reads === 1) {
				await route.fulfill({ status: 503, json: { detail: 'Execution storage is unavailable.' } });
				return;
			}
			await route.fulfill({
				json: { deployments: [deploymentFixture()], limit: 200, offset: 0, returned: 1 }
			});
		}
	);
	await openHome(page);

	const card = page.getByTestId('your-bots');
	await expect(card).toContainText("Couldn't load your bots: Execution storage is unavailable.");
	await expect(tile(page, 'bots-value')).toContainText('—');
	await expect(tile(page, 'portfolio-value-value')).toHaveText('$2,418.62');
	await card.getByRole('button', { name: 'Retry' }).click();
	await expect(card.getByTestId('home-bot-row')).toHaveCount(1);
	await expect(tile(page, 'bots-value')).toHaveText(/1\s*running/);
});

// ---------------------------------------------------------------- holdings

async function openWithAssets(
	page: Page,
	portfolio: typeof demoPortfolio | typeof pagedPortfolio
): Promise<void> {
	await mockHome(page, { portfolio });
	await openHome(page);
	await expect(page.getByRole('heading', { name: 'Holdings' })).toBeVisible();
}

const holdingRows = (page: Page) => page.getByTestId('holdings').getByTestId('holding-row');

test('shows a compact holdings card with the ten largest balances and Show all', async ({
	page
}) => {
	await openWithAssets(page, pagedPortfolio);

	await expect(holdingRows(page)).toHaveCount(10);
	// Token 0 is valued below the dust threshold and collapses into the summary.
	await expect(page.getByTestId('holdings-range')).toHaveText('Showing 10 of 11');
	const showAll = page.getByRole('button', { name: 'Show all 11' });
	await expect(showAll).toHaveAttribute('aria-expanded', 'false');
	await showAll.click();
	await expect(holdingRows(page)).toHaveCount(11);
	await expect(page.getByTestId('holdings-range')).toHaveText('Showing 11 of 11');
	await page.getByRole('button', { name: 'Show top 10' }).click();
	await expect(holdingRows(page)).toHaveCount(10);
});

test('sorts holdings by clicking headers with a three-state cycle', async ({ page }) => {
	await openWithAssets(page, demoPortfolio);

	const valueHeader = page.getByRole('columnheader', { name: 'Est. value' });
	const firstRow = holdingRows(page).first();

	// Largest holdings first without any interaction: the honest default.
	await expect(valueHeader).toHaveAttribute('aria-sort', 'descending');
	await expect(firstRow).toContainText('BTC');

	await valueHeader.click();
	await expect(valueHeader).toHaveAttribute('aria-sort', 'none');
	await expect(page.getByRole('columnheader', { name: 'Asset', exact: true })).toHaveAttribute(
		'aria-sort',
		'none'
	);

	await valueHeader.click();
	await expect(valueHeader).toHaveAttribute('aria-sort', 'ascending');
	await expect(firstRow).toContainText('ETH');

	await valueHeader.click();
	await expect(valueHeader).toHaveAttribute('aria-sort', 'descending');
	await expect(firstRow).toContainText('BTC');
});

test('sorts before trimming so the compact view shows the globally largest values', async ({
	page
}) => {
	await openWithAssets(page, pagedPortfolio);

	const rows = holdingRows(page);
	await expect(page.getByRole('columnheader', { name: 'Est. value' })).toHaveAttribute(
		'aria-sort',
		'descending'
	);
	await expect(rows).toHaveCount(10);
	await expect(rows.first()).toContainText('Token 11');
	await expect(rows.nth(9)).toContainText('Token 2');

	await page.getByRole('columnheader', { name: 'Est. value' }).click();
	await expect(page.getByRole('columnheader', { name: 'Est. value' })).toHaveAttribute(
		'aria-sort',
		'none'
	);
	// Token 0 ($0.00) is dust; Token 1 ($10) leads the unsorted order.
	await expect(rows.first()).toContainText('Token 1');
});

test('collapses sub-threshold balances into an expandable dust summary', async ({ page }) => {
	await openWithAssets(page, pagedPortfolio);

	const dustToggle = page.getByRole('button', { name: /balance under \$0.10/ });
	await expect(dustToggle).toContainText('1 balance under $0.10 totaling $0.00');

	await dustToggle.click();

	await expect(page.getByTestId('holdings').getByRole('listitem')).toContainText('Token 0 (TST0)');
	await expect(dustToggle).toHaveAttribute('aria-expanded', 'true');
});

test('labels the value source so ledger cash and venue balances stay distinct', async ({
	page
}) => {
	await mockHome(page);
	await openHome(page);
	await expect(page.getByTestId('holdings')).toContainText('USD estimate · Coinbase spot balances');
});

test('refreshes balances and presents a redacted connection error', async ({ page }) => {
	await mockHome(page);
	let requests = 0;
	await page.route('**/api/v1/portfolio', async (route) => {
		requests += 1;
		if (requests === 1) {
			await route.fulfill({ json: demoPortfolio });
			return;
		}
		await route.fulfill({
			status: 502,
			json: {
				detail: {
					code: 'coinbase_unavailable',
					message: 'Coinbase is temporarily unavailable. Try again shortly.'
				}
			}
		});
	});
	await openHome(page);
	await expect(tile(page, 'portfolio-value')).toContainText('$98,542.17');
	await page.getByRole('button', { name: 'Refresh balances' }).click();

	await expect(page.getByRole('alert')).toContainText(
		'Coinbase is temporarily unavailable. Try again shortly.'
	);
	await expect(page.getByTestId('connection-status')).toContainText('Coinbase refresh failed');
	await expect(tile(page, 'portfolio-value')).toContainText('$98,542.17');
	await expect(holdingRows(page)).toHaveCount(2);
	expect(requests).toBe(2);
});

// ---------------------------------------------------------------- fees

test('shows the fee tier as one compact line below the holdings', async ({ page }) => {
	await mockHome(page, { portfolio: demoPortfolio });
	await openHome(page);

	const holdingsHeading = page.getByRole('heading', { name: 'Holdings' });
	const feesHeading = page.getByRole('heading', { name: 'Fee tier' });
	await expect(feesHeading).toBeVisible();
	const holdingsBox = await holdingsHeading.boundingBox();
	const feesBox = await feesHeading.boundingBox();
	expect(holdingsBox).not.toBeNull();
	expect(feesBox).not.toBeNull();
	expect(holdingsBox!.y).toBeLessThan(feesBox!.y);
	await expect(page.getByText('0.60%')).toBeVisible();
	await expect(page.getByText('0.40%')).toBeVisible();
	await expect(page.getByText('Tier 1')).toBeVisible();
	await expect(page.getByText('$15,250.00')).toBeVisible();
	await expect(
		page.getByText(`As of ${new Date('2026-08-17T12:00:00Z').toLocaleString()}`)
	).toBeVisible();
	await expect(page.getByRole('row', { name: /Bitcoin BTC/ })).toContainText('0.76');
	await expect(page.getByRole('row', { name: /Ethereum ETH/ })).toContainText('$7,342.17');
});

test('formats tiny fee rates with exact decimal arithmetic', async ({ page }) => {
	await mockHome(page, {
		fees: {
			...feeProfile,
			taker_fee_rate: '0.000049999999999999999999999999999999999999',
			maker_fee_rate: '0.000050000000000000000000000000000000000000',
			usd_volume_30d: '0',
			fee_tier: 'Precision test'
		}
	});
	await openHome(page);

	await expect(page.getByText('0.00%', { exact: true })).toBeVisible();
	await expect(page.getByText('0.01%', { exact: true })).toBeVisible();
});

test('shows a controlled unavailable state when the fees request fails with 502', async ({
	page
}) => {
	await mockHome(page);
	let reads = 0;
	await page.route('**/api/v1/fees', async (route) => {
		reads += 1;
		if (reads === 1) {
			await route.fulfill({
				status: 502,
				json: {
					detail: {
						code: 'fees_unavailable',
						message: 'Fee profile is temporarily unavailable.'
					}
				}
			});
			return;
		}
		await route.fulfill({ json: feeProfile });
	});
	await openHome(page);

	const fees = page.getByTestId('fee-tier');
	await expect(fees).toContainText('Fee profile is temporarily unavailable.');
	await expect(page.getByText(/As of /)).toHaveCount(0);
	await fees.getByRole('button', { name: 'Retry' }).click();
	await expect(fees).toContainText('Tier 1');
});

// ---------------------------------------------------------------- data health

test('keeps Data health closed by default and loads diagnostics only when opened', async ({
	page
}) => {
	await mockHome(page);
	const previews: string[] = [];
	page.on('request', (request) => {
		if (new URL(request.url()).pathname === '/api/v1/market-data/preview') {
			previews.push(request.url());
		}
	});
	await openHome(page);

	const health = page.getByTestId('data-health');
	await expect(health).not.toHaveAttribute('open', '');
	await expect(page.getByTestId('data-health-summary')).toHaveText('0 watched datasets');
	expect(previews).toEqual([]);

	await openDataHealth(page);
	await expect(health).toContainText('No datasets are watched yet.');
	await expect.poll(() => previews.length).toBeGreaterThan(0);
});

test('opens Data health from a #data-health link', async ({ page }) => {
	await mockHome(page);
	await page.goto('/#data-health');
	await expect(page.getByTestId('data-health')).toHaveAttribute('open', '');
	await expect(page.getByRole('region', { name: 'Data-source diagnostics' })).toBeVisible();
});

test('shows a visible range-coverage failure while recent candle diagnostics remain available', async ({
	page
}) => {
	await mockHome(page);
	await page.route('**/api/v1/market-data/products', async (route) => {
		await route.fulfill({
			json: {
				products: [
					{
						product_id: 'BTC-USD',
						base_currency: 'BTC',
						quote_currency: 'USD',
						price_increment: '0.01',
						base_increment: '0.00000001',
						quote_increment: '0.01',
						base_min_size: '0.0001',
						quote_min_size: '1',
						trading_enabled: true
					}
				]
			}
		});
	});
	await page.route('**/api/v1/market-data/preview?product_id=BTC-USD', async (route) => {
		await route.fulfill({
			json: {
				product: {
					product_id: 'BTC-USD',
					base_currency: 'BTC',
					quote_currency: 'USD',
					price_increment: '0.01',
					base_increment: '0.00000001',
					quote_increment: '0.01',
					base_min_size: '0.0001',
					quote_min_size: '1',
					trading_enabled: true
				},
				timeframe: '1h',
				as_of: '2026-07-29T00:00:00Z',
				quality: {
					candle_count: 24,
					gap_count: 0,
					missing_intervals: 0,
					latest_completed_at: '2026-07-28T23:00:00Z',
					stale: false
				}
			}
		});
	});
	await page.route('**/api/v1/market-data/range?product_id=BTC-USD', async (route) => {
		await route.fulfill({ status: 502, json: { detail: { code: 'coinbase_unavailable' } } });
	});
	await openHome(page);
	await openDataHealth(page);

	await expect(page.getByText('Recent hourly candles are complete and contiguous')).toBeVisible();
	await expect(page.getByText('7-day range coverage unavailable')).toBeVisible();
});

function ingestionBody(overrides: Record<string, unknown> = {}): Record<string, unknown> {
	return {
		provider: 'demo',
		product_id: 'BTC-USD',
		timeframe: '1h',
		status: 'succeeded',
		last_attempt_at: '2026-07-29T02:05:00Z',
		last_success_at: '2026-07-29T02:05:00Z',
		requested_starts_at: '2026-07-22T02:00:00Z',
		requested_ends_at: '2026-07-29T02:00:00Z',
		fresh: true,
		enabled: true,
		freshness: 'current',
		watch_complete: true,
		coverage_status: 'complete',
		expected_latest_boundary: '2026-07-29T02:00:00Z',
		next_attempt_at: '2026-07-29T02:10:00Z',
		dataset_revision: 4,
		maintenance_kind: 'incremental',
		coverage: {
			starts_at: '2026-07-22T02:00:00Z',
			ends_at: '2026-07-29T02:00:00Z',
			expected_candle_count: 168,
			received_candle_count: 168,
			gap_count: 0,
			missing_intervals: 0,
			complete: true,
			content_fingerprint: `sha256:${'a'.repeat(64)}`
		},
		failure: null,
		...overrides
	};
}

test('shows durable market-data worker coverage and freshness evidence', async ({ page }) => {
	await mockHome(page);
	await page.route('**/api/v1/market-data/freshness*', async (route) => {
		await route.fulfill({
			json: {
				product_id: 'BTC-USD',
				newest_candle_at: '2026-08-17T12:00:00Z',
				as_of: '2026-08-17T13:00:00Z',
				age_seconds: 3600,
				status: 'fresh'
			}
		});
	});
	await page.route('**/api/v1/market-data/ingestion*', async (route) => {
		await route.fulfill({ json: ingestionBody() });
	});
	await openHome(page);
	await openDataHealth(page);

	await expect(page.getByText('Durable ingestion worker')).toBeVisible();
	await expect(page.getByText('Candle: fresh')).toBeVisible();
	await expect(page.getByText('168 / 168 candles')).toBeVisible();
	await expect(page.getByText('Demo dataset')).toBeVisible();
	await expect(page.getByText('Current · complete')).toBeVisible();
	await expect(page.getByText('Revision 4 · incremental')).toBeVisible();
	await expect(page.getByText(/Next check/)).toBeVisible();
});

test('keeps redacted market-data worker failures visible', async ({ page }) => {
	await mockHome(page);
	await page.route('**/api/v1/market-data/ingestion*', async (route) => {
		await route.fulfill({
			json: ingestionBody({
				status: 'failed',
				last_success_at: '2026-07-29T01:05:00Z',
				fresh: false,
				freshness: 'stale',
				expected_latest_boundary: '2026-07-29T01:00:00Z',
				next_attempt_at: null,
				dataset_revision: 3,
				failure: {
					code: 'provider_unavailable',
					message: 'Historical market-data retrieval failed.',
					consecutive_failures: 2
				}
			})
		});
	});
	await openHome(page);
	await openDataHealth(page);

	await expect(page.getByText('Last attempt failed')).toBeVisible();
	await expect(page.getByText('Historical market-data retrieval failed.')).toBeVisible();
	await expect(page.getByText('2 consecutive failures')).toBeVisible();
	await expect(page.getByText('Last verified coverage · Stale · complete')).toBeVisible();
	await expect(page.getByText('168 / 168 candles')).toBeVisible();
});

test('keeps the retained failure count visible while ingestion retries', async ({ page }) => {
	await mockHome(page);
	await page.route('**/api/v1/market-data/ingestion*', async (route) => {
		await route.fulfill({
			json: {
				provider: 'demo',
				product_id: 'BTC-USD',
				timeframe: '1h',
				status: 'running',
				last_attempt_at: '2026-07-29T02:10:00Z',
				last_success_at: null,
				requested_starts_at: '2026-07-22T02:00:00Z',
				requested_ends_at: '2026-07-29T02:00:00Z',
				fresh: null,
				coverage: null,
				failure: {
					code: 'provider_unavailable',
					message: 'Historical market-data retrieval failed.',
					consecutive_failures: 2
				}
			}
		});
	});
	await openHome(page);
	await openDataHealth(page);

	await expect(page.getByText('Retrieving and validating a bounded hourly range.')).toBeVisible();
	await expect(
		page.getByText('2 consecutive failures remain recorded until success.')
	).toBeVisible();
});

test('keeps worker evidence visible when the recent-candle preview fails', async ({ page }) => {
	await mockHome(page);
	await page.route('**/api/v1/market-data/preview*', async (route) => {
		await route.fulfill({ status: 502, contentType: 'application/json', body: '{}' });
	});
	await page.route('**/api/v1/market-data/ingestion*', async (route) => {
		await route.fulfill({
			json: ingestionBody({
				last_success_at: '2026-07-29T02:05:02Z',
				coverage: {
					starts_at: '2026-07-22T02:00:00Z',
					ends_at: '2026-07-29T02:00:00Z',
					expected_candle_count: 168,
					received_candle_count: 168,
					gap_count: 0,
					missing_intervals: 0,
					complete: true,
					content_fingerprint: `sha256:${'b'.repeat(64)}`
				}
			})
		});
	});
	await openHome(page);
	await openDataHealth(page);

	await expect(page.getByText('Durable ingestion worker')).toBeVisible();
	await expect(page.getByText('Current · complete')).toBeVisible();
});

test('shows watch incompleteness when the island does not span the watch', async ({ page }) => {
	await mockHome(page);
	await page.route('**/api/v1/market-data/ingestion*', async (route) => {
		await route.fulfill({
			json: ingestionBody({ watch_complete: false, coverage_status: 'gap_detected' })
		});
	});
	await openHome(page);
	await openDataHealth(page);

	await expect(page.getByText('Current · watch incomplete')).toBeVisible();
	await expect(page.getByText('168 / 168 candles')).toBeVisible();
});

test('shows a controlled freshness failure state when the freshness endpoint fails', async ({
	page
}) => {
	await mockHome(page);
	await page.route('**/api/v1/market-data/freshness*', async (route) => {
		await route.fulfill({
			status: 503,
			json: { detail: { code: 'market_data_worker_state_unavailable' } }
		});
	});
	await openHome(page);
	await openDataHealth(page);

	await expect(page.getByText('Candle: unavailable')).toBeVisible();
});

test('shows public ticker feed lifecycle separately from candle freshness', async ({ page }) => {
	await mockHome(page);
	await page.route('**/api/v1/market-data/feed*', async (route) => {
		await route.fulfill({
			json: {
				product_id: 'BTC-USD',
				state: 'connected',
				last_message_at: '2026-08-17T12:00:00Z',
				last_ticker_at: '2026-08-17T12:00:00Z',
				last_price: '65000.50',
				updated_at: '2026-08-17T12:00:00Z'
			}
		});
	});
	await openHome(page);
	await openDataHealth(page);

	await expect(page.getByText('Feed: connected')).toBeVisible();
});

test('shows a controlled feed failure state when the feed endpoint fails', async ({ page }) => {
	await mockHome(page);
	await page.route('**/api/v1/market-data/feed*', async (route) => {
		await route.fulfill({
			status: 503,
			json: { detail: { code: 'market_feed_state_unavailable' } }
		});
	});
	await openHome(page);
	await openDataHealth(page);

	await expect(page.getByText('Feed: unavailable')).toBeVisible();
});

// ---------------------------------------------------------------- motion

test('loading skeletons hold still under prefers-reduced-motion', async ({ page }) => {
	await page.emulateMedia({ reducedMotion: 'reduce' });
	await mockHome(page);
	await page.route('**/api/v1/portfolio', () => new Promise<void>(() => undefined));
	await page.route('**/api/v1/portfolio/history**', () => new Promise<void>(() => undefined));
	await page.goto('/');

	const skeleton = tile(page, 'portfolio-value').locator('.skeleton').first();
	await expect(skeleton).toBeVisible();
	expect(await skeleton.evaluate((node) => getComputedStyle(node).animationName)).toBe('none');

	await page.emulateMedia({ reducedMotion: 'no-preference' });
	await expect
		.poll(() => skeleton.evaluate((node) => getComputedStyle(node).animationName))
		.toBe('shimmer');
});
