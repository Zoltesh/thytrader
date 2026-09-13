import { expect, test, type Page } from '@playwright/test';

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

type HistorySnapshot = {
	as_of: string;
	total_value: { amount: string; currency: 'USD' };
};

function historyEntry(amount: string, asOf: string): HistorySnapshot {
	return { as_of: asOf, total_value: { amount, currency: 'USD' } };
}

async function openPortfolioWithHistory(
	page: Page,
	history: { status: number } | { entries: HistorySnapshot[]; sampling_interval_seconds?: number }
): Promise<void> {
	await page.route('**/api/v1/portfolio', async (route) => {
		await route.fulfill({ json: demoPortfolio });
	});
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
				range: '24h',
				sampling_interval_seconds: history.sampling_interval_seconds ?? 300
			}
		});
	});
	await page.goto('/');
	await expect(page.getByRole('heading', { name: 'Portfolio history' })).toBeVisible();
}

test('shows a practical demo portfolio and detected extra permissions', async ({ page }) => {
	await page.route('**/api/v1/portfolio', async (route) => {
		await route.fulfill({ json: demoPortfolio });
	});
	await page.route('**/api/v1/fees', async (route) => {
		await route.fulfill({
			json: {
				taker_fee_rate: '0.0060',
				maker_fee_rate: '0.0040',
				usd_volume_30d: '15250.00',
				fee_tier: 'Tier 1',
				as_of: '2026-08-17T12:00:00Z',
				source: 'coinbase'
			}
		});
	});

	await page.goto('/');

	await expect(page.getByRole('heading', { name: 'Your portfolio' })).toBeVisible();
	await expect(page.getByText('Demo data', { exact: true })).toBeVisible();
	await expect(page.getByText('$98,542.17')).toBeVisible();
	const assetsHeading = page.getByRole('heading', { name: 'Assets' });
	const feesHeading = page.getByRole('heading', { name: 'Fee Tier & Costs' });
	await expect(assetsHeading).toBeVisible();
	await expect(feesHeading).toBeVisible();
	const assetsBox = await assetsHeading.boundingBox();
	const feesBox = await feesHeading.boundingBox();
	expect(assetsBox).not.toBeNull();
	expect(feesBox).not.toBeNull();
	expect(assetsBox!.y).toBeLessThan(feesBox!.y);
	await expect(page.getByText('0.60%')).toBeVisible();
	await expect(page.getByText('0.40%')).toBeVisible();
	await expect(page.getByText('Tier 1')).toBeVisible();
	await expect(
		page.getByText(`As of ${new Date('2026-08-17T12:00:00Z').toLocaleString()}`)
	).toBeVisible();
	await expect(page.getByRole('row', { name: /Bitcoin BTC/ })).toContainText('0.76000000');
	await expect(page.getByRole('row', { name: /Ethereum ETH/ })).toContainText('$7,342.17');
	await expect(page.getByText('View', { exact: true })).toBeVisible();
	await expect(page.getByText('Trade', { exact: true })).toBeVisible();
	await expect(page.getByText('Transfer', { exact: true })).toBeVisible();
});

test('formats tiny fee rates with exact decimal arithmetic', async ({ page }) => {
	await page.route('**/api/v1/portfolio', async (route) => {
		await route.fulfill({ json: demoPortfolio });
	});
	await page.route('**/api/v1/fees', async (route) => {
		await route.fulfill({
			json: {
				taker_fee_rate: '0.000049999999999999999999999999999999999999',
				maker_fee_rate: '0.000050000000000000000000000000000000000000',
				usd_volume_30d: '0',
				fee_tier: 'Precision test',
				as_of: '2026-08-17T12:00:00Z',
				source: 'coinbase'
			}
		});
	});

	await page.goto('/');

	await expect(page.getByText('0.00%', { exact: true })).toBeVisible();
	await expect(page.getByText('0.01%', { exact: true })).toBeVisible();
});

test('loads demo portfolio through the real SvelteKit and FastAPI processes', async ({ page }) => {
	await page.goto('/');

	await expect(page.getByText('Demo data', { exact: true })).toBeVisible();
	await expect(page.getByText('$99,792.17')).toBeVisible();
	await expect(page.getByRole('heading', { name: 'Fee Tier & Costs' })).toBeVisible();
	await expect(page.getByText('0.60%')).toBeVisible();
	await expect(
		page.getByText(`As of ${new Date('2026-08-17T12:00:00Z').toLocaleString()}`)
	).toBeVisible();
	await expect(page.getByRole('row', { name: /Bitcoin BTC/ })).toBeVisible();
});

test('shows controlled unavailable state when fees request fails with 502', async ({ page }) => {
	await page.route('**/api/v1/portfolio', async (route) => {
		await route.fulfill({ json: demoPortfolio });
	});
	await page.route('**/api/v1/fees', async (route) => {
		await route.fulfill({
			status: 502,
			json: {
				detail: {
					code: 'fees_unavailable',
					message: 'Fee profile is temporarily unavailable.'
				}
			}
		});
	});

	await page.goto('/');

	await expect(page.getByRole('heading', { name: 'Your portfolio' })).toBeVisible();
	await expect(page.getByRole('heading', { name: 'Fee Tier & Costs' })).toBeVisible();
	await expect(page.getByText('Fee profile is temporarily unavailable.')).toBeVisible();
	await expect(page.getByText(/As of /)).toHaveCount(0);
});

test('refreshes the portfolio and presents a redacted connection error', async ({ page }) => {
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

	await page.goto('/');
	await expect(page.getByText('$98,542.17')).toBeVisible();
	await page.getByRole('button', { name: 'Refresh portfolio' }).click();

	await expect(page.getByRole('alert')).toContainText(
		'Coinbase is temporarily unavailable. Try again shortly.'
	);
	await expect(page.getByText('$98,542.17')).toBeVisible();
	expect(requests).toBe(2);
});

test('shows a visible range-coverage failure while recent candle diagnostics remain available', async ({
	page
}) => {
	await page.route('**/api/v1/portfolio', async (route) => {
		await route.fulfill({ json: demoPortfolio });
	});
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

	await page.goto('/');

	await expect(page.getByText('Recent hourly candles are complete and contiguous')).toBeVisible();
	await expect(page.getByText('7-day range coverage unavailable')).toBeVisible();
});

test('shows durable market-data worker coverage and freshness evidence', async ({ page }) => {
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
		await route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({
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
				failure: null
			})
		});
	});

	await page.goto('/');

	await expect(page.getByText('Durable ingestion worker')).toBeVisible();
	await expect(page.getByText('Candle: fresh')).toBeVisible();
	await expect(page.getByText('168 / 168 candles')).toBeVisible();
	await expect(page.getByText('Demo dataset')).toBeVisible();
	await expect(page.getByText('Current · complete')).toBeVisible();
	await expect(page.getByText('Revision 4 · incremental')).toBeVisible();
	await expect(page.getByText(/Next check/)).toBeVisible();
});

test('keeps redacted market-data worker failures visible', async ({ page }) => {
	await page.route('**/api/v1/market-data/ingestion*', async (route) => {
		await route.fulfill({
			json: {
				provider: 'demo',
				product_id: 'BTC-USD',
				timeframe: '1h',
				status: 'failed',
				last_attempt_at: '2026-07-29T02:05:00Z',
				last_success_at: '2026-07-29T01:05:00Z',
				requested_starts_at: '2026-07-22T02:00:00Z',
				requested_ends_at: '2026-07-29T02:00:00Z',
				fresh: false,
				enabled: true,
				freshness: 'stale',
				coverage_status: 'complete',
				expected_latest_boundary: '2026-07-29T01:00:00Z',
				next_attempt_at: null,
				dataset_revision: 3,
				maintenance_kind: 'incremental',
				coverage: {
					starts_at: '2026-07-22T01:00:00Z',
					ends_at: '2026-07-29T01:00:00Z',
					expected_candle_count: 168,
					received_candle_count: 168,
					gap_count: 0,
					missing_intervals: 0,
					complete: true,
					content_fingerprint: `sha256:${'b'.repeat(64)}`
				},
				failure: {
					code: 'provider_unavailable',
					message: 'Historical market-data retrieval failed.',
					consecutive_failures: 2
				}
			}
		});
	});

	await page.goto('/');

	await expect(page.getByText('Last attempt failed')).toBeVisible();
	await expect(page.getByText('Historical market-data retrieval failed.')).toBeVisible();
	await expect(page.getByText('2 consecutive failures')).toBeVisible();
	await expect(page.getByText('Last verified coverage · Stale · complete')).toBeVisible();
	await expect(page.getByText('168 / 168 candles')).toBeVisible();
});

test('keeps the retained failure count visible while ingestion retries', async ({ page }) => {
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

	await page.goto('/');

	await expect(page.getByText('Retrieving and validating a bounded hourly range.')).toBeVisible();
	await expect(
		page.getByText('2 consecutive failures remain recorded until success.')
	).toBeVisible();
});

test('keeps worker evidence visible when the recent-candle preview fails', async ({ page }) => {
	await page.route('**/api/v1/market-data/preview*', async (route) => {
		await route.fulfill({ status: 502, contentType: 'application/json', body: '{}' });
	});
	await page.route('**/api/v1/market-data/ingestion*', async (route) => {
		await route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({
				provider: 'demo',
				product_id: 'BTC-USD',
				timeframe: '1h',
				status: 'succeeded',
				last_attempt_at: '2026-07-29T02:05:00Z',
				last_success_at: '2026-07-29T02:05:02Z',
				requested_starts_at: '2026-07-22T02:00:00Z',
				requested_ends_at: '2026-07-29T02:00:00Z',
				fresh: true,
				enabled: true,
				freshness: 'current',
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
					content_fingerprint: `sha256:${'b'.repeat(64)}`
				},
				failure: null
			})
		});
	});

	await page.goto('/');

	await expect(page.getByText('Durable ingestion worker')).toBeVisible();
	await expect(page.getByText('Current · complete')).toBeVisible();
});

test('shows controlled freshness failure state when the freshness endpoint fails', async ({
	page
}) => {
	await page.route('**/api/v1/market-data/freshness*', async (route) => {
		await route.fulfill({
			status: 503,
			json: { detail: { code: 'market_data_worker_state_unavailable' } }
		});
	});

	await page.goto('/');

	await expect(page.getByText('Candle: unavailable')).toBeVisible();
});

test('shows public ticker feed lifecycle separately from candle freshness', async ({ page }) => {
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

	await page.goto('/');

	await expect(page.getByText('Feed: connected')).toBeVisible();
});

test('shows controlled feed failure state when the feed endpoint fails', async ({ page }) => {
	await page.route('**/api/v1/market-data/feed*', async (route) => {
		await route.fulfill({
			status: 503,
			json: { detail: { code: 'market_feed_state_unavailable' } }
		});
	});

	await page.goto('/');

	await expect(page.getByText('Feed: unavailable')).toBeVisible();
});

test('labels history sampling as a target interval rather than a guaranteed cadence', async ({
	page
}) => {
	await openPortfolioWithHistory(page, {
		entries: [
			historyEntry('120', '2026-07-27T12:00:00Z'),
			historyEntry('110', '2026-07-27T11:00:00Z'),
			historyEntry('100', '2026-07-27T10:00:00Z')
		],
		sampling_interval_seconds: 3600
	});

	const history = page.getByRole('region', { name: 'Portfolio value history' });
	await expect(history).toContainText('3 sampled snapshots');
	await expect(history).toContainText('target interval 1h');
	await expect(history).not.toContainText('every');
});

test('shows empty history copy and does not invent snapshots on refresh', async ({ page }) => {
	await openPortfolioWithHistory(page, { entries: [] });

	await expect(page.getByText('No snapshots exist in this range yet.')).toBeVisible();
	await expect(
		page.getByText(
			'The worker records live portfolios automatically; Refresh never creates chart points.'
		)
	).toBeVisible();

	await page.getByRole('button', { name: 'Refresh portfolio' }).click();

	await expect(page.getByText('No snapshots exist in this range yet.')).toBeVisible();
	await expect(page.getByTestId('portfolio-history-chart')).toHaveCount(0);
});

test('shows single-snapshot history copy until a line can be drawn', async ({ page }) => {
	await openPortfolioWithHistory(page, {
		entries: [historyEntry('100', '2026-07-27T10:00:00Z')]
	});

	await expect(page.getByText('One snapshot is available.')).toBeVisible();
	await expect(
		page.getByText('A line appears after the next successful scheduled observation.')
	).toBeVisible();
	await expect(page.getByTestId('portfolio-history-chart')).toHaveCount(0);
});

test('shows unavailable history copy when persistence is disabled', async ({ page }) => {
	await openPortfolioWithHistory(page, { status: 503 });

	await expect(
		page.getByText('Portfolio history is unavailable on this installation.')
	).toBeVisible();
	await expect(
		page.getByText('Start the full local stack to enable durable scheduled snapshots.')
	).toBeVisible();
});

test('shows failed history copy when the history API errors', async ({ page }) => {
	await openPortfolioWithHistory(page, { status: 502 });

	await expect(page.getByText('Portfolio history could not be loaded.')).toBeVisible();
	await expect(page.getByText('Try again after the API and worker report healthy.')).toBeVisible();
});

test('places history points by wall-clock time and notes an orphan post-gap snapshot', async ({
	page
}) => {
	await openPortfolioWithHistory(page, {
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
	await openPortfolioWithHistory(page, {
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
