/**
 * Test / Run stages name the exact missing product × timeframe dataset and offer
 * a confirmation-gated "Download data" (watch-add + no-wait ingest) with progress.
 * Every data-lane call is mocked; nothing reaches Coinbase.
 */
import type { Page } from '@playwright/test';
import { expect, test } from '../e2e/harness';
import {
	datasetFingerprint,
	definition,
	mockBacktestList,
	mockDeployments,
	mockStrategy,
	strategyId,
	strategyRecord
} from '../e2e/workspace-fixtures';

const htfDefinition = {
	...definition,
	htf_filter: {
		timeframe: '2h',
		data_requirements: {
			warmup_bars: 50,
			required_fields: ['open', 'high', 'low', 'close', 'volume']
		},
		indicators: [
			{ id: 'htf_ema_fast', kind: 'ema', input: 'close', parameters: { period: 20 } },
			{ id: 'htf_ema_slow', kind: 'ema', input: 'close', parameters: { period: 50 } }
		],
		when: {
			all: [
				{
					left: { indicator: 'htf_ema_fast' },
					operator: 'greater_than',
					right: { indicator: 'htf_ema_slow' }
				}
			]
		}
	}
};

function recentEnd(): string {
	return new Date(Date.now() - 60 * 60 * 1000).toISOString();
}

type DataLaneCalls = { watchBodies: unknown[]; ingestBodies: unknown[]; polls: number };

async function mockDataLane(page: Page): Promise<DataLaneCalls> {
	const calls: DataLaneCalls = { watchBodies: [], ingestBodies: [], polls: 0 };
	let downloaded = false;
	await page.route('**/api/v1/market-data/datasets/latest', (route) =>
		route.fulfill({
			json: {
				datasets: [
					{
						product_id: 'BTC-USDC',
						timeframe: '1h',
						starts_at: '2025-08-01T00:00:00Z',
						ends_at: recentEnd(),
						content_fingerprint: datasetFingerprint
					},
					...(downloaded
						? [
								{
									product_id: 'BTC-USDC',
									timeframe: '2h',
									starts_at: '2026-05-09T00:00:00Z',
									ends_at: recentEnd(),
									content_fingerprint: `sha256:${'2'.repeat(64)}`
								}
							]
						: [])
				]
			}
		})
	);
	await page.route('**/api/v1/data/watchlist', async (route) => {
		if (route.request().method() === 'PUT') {
			calls.watchBodies.push(route.request().postDataJSON());
			await route.fulfill({
				json: {
					target: {
						provider: 'coinbase',
						product_id: 'BTC-USDC',
						timeframe: '2h',
						lookback_hours: 87600,
						enabled: true
					}
				}
			});
			return;
		}
		await route.fulfill({ json: { targets: [] } });
	});
	await page.route('**/api/v1/data/ingest**', async (route) => {
		if (route.request().method() === 'POST') {
			calls.ingestBodies.push(route.request().postDataJSON());
			await route.fulfill({
				status: 202,
				json: {
					accepted: true,
					product_id: 'BTC-USDC',
					timeframe: '2h',
					ingest_requested_at: new Date().toISOString(),
					state: {
						status: 'never_run',
						watch_complete: false,
						complete: false,
						watch_expected_candle_count: 4380
					}
				}
			});
			return;
		}
		calls.polls += 1;
		downloaded = true;
		await route.fulfill({
			json: {
				product_id: 'BTC-USDC',
				timeframe: '2h',
				ingest_requested_at: null,
				state: {
					status: 'succeeded',
					watch_complete: true,
					complete: true,
					watch_expected_candle_count: 4380,
					received_candle_count: 1574,
					expected_candle_count: 1574,
					history_floor_at: '2026-05-09T00:00:00+00:00'
				}
			}
		});
	});
	return calls;
}

test('Test stage names the missing HTF dataset and downloads it behind a confirmation', async ({
	page
}) => {
	await mockStrategy(page, {
		record: strategyRecord({ document: htfDefinition, strategy: htfDefinition })
	});
	await mockBacktestList(page);
	const calls = await mockDataLane(page);
	await page.goto(`/strategies/${strategyId}/test`);

	const readiness = page.getByTestId('data-readiness');
	await expect(readiness).toContainText(
		"No verified BTC-USDC × 2h dataset yet. This strategy's higher-timeframe filter clock needs it."
	);
	await expect(readiness).toContainText('The higher-timeframe filter is optional.');
	await expect(
		readiness.getByRole('link', { name: 'Change or remove the HTF filter in Build' })
	).toHaveAttribute('href', `/strategies/${strategyId}?section=entry`);
	await expect(page.getByRole('button', { name: 'Run backtest' })).toBeDisabled();

	await readiness.getByRole('button', { name: 'Download data…' }).click();
	const dialog = page.getByTestId('data-download-dialog');
	await expect(dialog).toContainText('BTC-USDC 2h (HTF filter)');
	await expect(dialog).toContainText('87600-hour (10 years)');
	expect(calls.watchBodies).toHaveLength(0);
	await dialog.getByRole('button', { name: 'Download data' }).click();

	await expect(
		page.getByRole('status').filter({ hasText: 'Complete · 1574 of 4380 candles' })
	).toBeVisible();
	await expect(
		page.getByText('Coinbase has no trades before 2026-05-09 00:00 UTC (the listing)')
	).toBeVisible();
	expect(calls.watchBodies).toEqual([
		{ product_id: 'BTC-USDC', timeframe: '2h', lookback_hours: 87600, enabled: true }
	]);
	expect(calls.ingestBodies).toEqual([{ product_id: 'BTC-USDC', timeframe: '2h' }]);
	await expect(page.getByTestId('data-readiness-2h')).toHaveCount(0);
});

test('cancelling the download confirmation queues nothing', async ({ page }) => {
	await mockStrategy(page, {
		record: strategyRecord({ document: htfDefinition, strategy: htfDefinition })
	});
	await mockBacktestList(page);
	const calls = await mockDataLane(page);
	await page.goto(`/strategies/${strategyId}/test`);
	await page.getByRole('button', { name: 'Download data…' }).click();
	await page.getByTestId('data-download-dialog').getByRole('button', { name: 'Cancel' }).click();
	await expect(page.getByTestId('data-download-dialog')).toBeHidden();
	expect(calls.watchBodies).toHaveLength(0);
	expect(calls.ingestBodies).toHaveLength(0);
});

test('Run stage names the missing HTF clock for paper and live', async ({ page }) => {
	await mockStrategy(page, {
		record: strategyRecord({ document: htfDefinition, strategy: htfDefinition })
	});
	await mockDeployments(page, () => []);
	await mockDataLane(page);
	await page.goto(`/strategies/${strategyId}/run`);
	const readiness = page.getByTestId('data-readiness');
	await expect(readiness).toContainText('missing coverage pauses the bot');
	await expect(readiness).toContainText('No verified BTC-USDC × 2h dataset yet.');
});

test('the Build link opens Entry where the optional HTF filter can be removed', async ({
	page
}) => {
	await mockStrategy(page, {
		record: strategyRecord({ document: htfDefinition, strategy: htfDefinition })
	});
	await page.goto(`/strategies/${strategyId}?section=entry`);
	const remove = page.getByTestId('remove-htf-filter');
	await expect(remove).toHaveText('Remove higher-timeframe filter (2h)');
	await remove.click();
	await expect(page.getByLabel('Enable higher-timeframe filter (optional)')).not.toBeChecked();
	await expect(page.getByText('Off by default.')).toBeVisible();
});
