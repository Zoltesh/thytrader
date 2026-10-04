import { expect, test } from '../e2e/harness';
import { strategyId } from '../e2e/workspace-fixtures';

test('economic preflight uses the actual API calculation and leaves trading untouched', async ({
	page
}) => {
	await page.route('**/api/v1/research/campaigns?*', (route) => route.fulfill({ json: [] }));
	await page.goto('/research');
	await expect(page.getByRole('button', { name: 'Calculate economics' })).toBeEnabled();
	await expect(page.getByRole('heading', { name: 'Research', exact: true })).toBeVisible();
	await page.getByRole('button', { name: 'Calculate economics' }).click();
	await expect(page.getByText('Target does not clear modeled costs or is absent.')).toBeVisible();
	await expect(page.getByText(/Net target PnL: -0.5025/)).toBeVisible();
});

test('campaign creation sends frozen UTC windows, sample gates and explicit stress', async ({
	page
}) => {
	let posted: unknown;
	await page.route('**/api/v1/research/campaigns*', async (route) => {
		if (route.request().method() === 'POST') {
			posted = route.request().postDataJSON();
			expect(route.request().headers()['x-csrf-token']).toBeTruthy();
			await route.fulfill({ status: 201, json: {} });
		} else await route.fulfill({ json: [] });
	});
	await page.route('**/api/v1/strategies?*', (route) =>
		route.fulfill({
			json: {
				strategies: [
					{
						strategy_id: strategyId,
						name: 'Frozen BTC',
						product_id: 'BTC-USDC',
						timeframe: '1h',
						revision: 1
					}
				],
				has_more: false,
				next_cursor: null
			}
		})
	);
	await page.goto('/research');
	await expect(page.getByRole('button', { name: 'Calculate economics' })).toBeEnabled();
	await page.getByLabel('Name', { exact: true }).fill('Forward validation');
	await page.getByLabel(/^Type/).selectOption('prospective');
	await page.getByLabel('Strategies (select one or more)').selectOption(strategyId);
	await page.getByLabel('Start (UTC)').fill('2026-10-05T00:00');
	await page.getByLabel('End (UTC)').fill('2027-04-03T00:00');
	await page.getByLabel('Deadline (UTC)').fill('2027-04-05T00:00');
	await page.getByLabel('Stress execution assumptions').check();
	await page.getByLabel('Additional entry delay (bars)').fill('1');
	await page.getByLabel('Entry fill fraction').fill('0.5');
	await page.getByRole('button', { name: 'Freeze and authorize research' }).click();
	await expect
		.poll(() => posted)
		.toMatchObject({
			name: 'Forward validation',
			kind: 'prospective',
			deadline: '2027-04-05T00:00:00Z',
			gates: { minimum_trades: 10, maximum_drawdown_fraction: '0.15' },
			cases: [
				{
					request: {
						strategy_id: strategyId,
						evaluation_start: '2026-10-05T00:00:00Z',
						execution_stress: { entry_latency_bars: 1, entry_fill_fraction: '0.5' }
					}
				}
			]
		});
});
