import { expect, test } from '@playwright/test';

const fingerprint = `sha256:${'a'.repeat(64)}`;
const strategyFingerprint = `sha256:${'b'.repeat(64)}`;
const runFingerprint = `sha256:${'c'.repeat(64)}`;
const datasetFingerprint = `sha256:${'d'.repeat(64)}`;

const summary = {
	initial_equity: '10000',
	final_equity: '10198.15',
	total_net_pnl: '198.15',
	total_return_fraction: '0.019815',
	gross_profit: '198.15',
	gross_loss: '0',
	win_rate: '1',
	profit_factor: null,
	average_win: '198.15',
	average_loss: null,
	trade_count: 1,
	winning_trade_count: 1,
	maximum_drawdown: '0',
	maximum_drawdown_fraction: '0',
	exposure_bars: 1,
	evaluation_bars: 2
};

test('shows a published backtest summary then its immutable detail', async ({ page }) => {
	await page.route('**/api/v1/backtests', async (route) =>
		route.fulfill({
			json: {
				entries: [
					{
						result_fingerprint: fingerprint,
						run_fingerprint: runFingerprint,
						strategy_fingerprint: strategyFingerprint,
						dataset_fingerprint: datasetFingerprint,
						engine_contract_version: 'thytrader-bar-backtest-v1',
						published_at: '2026-08-03T17:25:34Z',
						summary
					}
				],
				limit: 50,
				offset: 0,
				returned: 1
			}
		})
	);
	await page.route('**/api/v1/backtests/**', async (route) =>
		route.fulfill({
			json: {
				result_fingerprint: fingerprint,
				result: {
					schema_version: '1.0',
					engine_contract_version: 'thytrader-bar-backtest-v1',
					run_fingerprint: runFingerprint,
					strategy_fingerprint: strategyFingerprint,
					dataset_fingerprint: datasetFingerprint,
					signal_trace_fingerprint: `sha256:${'e'.repeat(64)}`,
					summary,
					equity_curve: [
						{
							candle_starts_at: '2026-08-01T02:00:00Z',
							cash: '10000',
							base_quantity: '0',
							mark_price: '14',
							equity: '10000'
						}
					],
					trades: [
						{
							entry: {
								candle_starts_at: '2026-08-01T03:00:00Z',
								price: '15',
								quantity: '1',
								notional: '15',
								fee: '0.03',
								fee_rate: '0.002'
							},
							exit: {
								candle_starts_at: '2026-08-01T04:00:00Z',
								price: '27',
								quantity: '1',
								notional: '27',
								fee: '0.05',
								fee_rate: '0.002',
								reason: 'take_profit'
							},
							gross_pnl: '12',
							net_pnl: '11.92',
							holding_bars: 1
						}
					]
				}
			}
		})
	);
	await page.goto('/backtests');
	await expect(page.getByRole('heading', { name: 'Backtests', exact: true })).toBeVisible();
	await expect(page.getByText('Published backtests')).toBeVisible();
	await page.getByRole('button', { name: /Inspect/ }).click();
	await expect(page.getByText('Simulation result')).toBeVisible();
	await expect(page.getByText('Modeled assumptions')).toBeVisible();
	await expect(page.getByText('take profit')).toBeVisible();
});

test('explains an empty result list', async ({ page }) => {
	await page.route('**/api/v1/backtests', async (route) =>
		route.fulfill({ json: { entries: [], limit: 50, offset: 0, returned: 0 } })
	);
	await page.goto('/backtests');
	await expect(page.getByText('No backtest results are published yet.')).toBeVisible();
});
