import { expect, test } from '../e2e/harness';

/** Real demo API + UI processes; only the unavailable CFM mirror is synthesized. */
test('Home labels spot scope and keeps CFM cash separate', async ({ page }) => {
	await page.route('**/api/v1/operator/futures-account', (route) =>
		route.fulfill({
			json: {
				overall_status: 'degraded',
				components: [],
				payload: {
					observed_at: '2026-01-01T12:00:00Z',
					age_seconds: 240,
					stale: true,
					enablement: 'enabled',
					read_failures: ['positions:transport'],
					positions: null,
					balance: {
						currency: 'USD',
						total_usd_balance: '120',
						cbi_usd_balance: '80',
						cfm_usd_balance: '40',
						total_pending_transfers_amount: '30',
						daily_realized_pnl: '2',
						futures_buying_power: '1000',
						available_margin: '100',
						liquidation_threshold: '10',
						liquidation_buffer_amount: '90',
						liquidation_buffer_percentage: '90',
						funding_pnl: '0',
						unrealized_pnl: null
					}
				}
			}
		})
	);
	await page.goto('/');
	await expect(page.getByTestId('kpi-portfolio-value').getByRole('heading')).toHaveText(
		'Spot portfolio value'
	);
	await expect(page.getByTestId('portfolio-chart').getByRole('heading')).toHaveText(
		'Spot portfolio value'
	);
	await expect(page.getByTestId('holdings').getByRole('heading')).toHaveText('Spot holdings');
	await expect(page.getByTestId('portfolio-chart')).toContainText('Excludes futures');
	const card = page.getByTestId('futures-card');
	await expect(card).toContainText('CFM-reported total cash (USD)');
	await expect(card).toContainText('$120.00');
	await expect(card).toContainText('Pending transfers (USD)');
	await expect(card).toContainText('$30.00');
	await expect(card).toContainText('Daily realized PnL (USD)');
	await expect(card).toContainText('+$2.00');
	await expect(card).toContainText('Stale snapshot');
	await expect(card).toContainText('2026-01-01T12:00:00Z');
	await expect(card).toContainText('positions:transport');
	await expect(card).toContainText('not extra money');
});

test('Home does not hide an initial futures report failure', async ({ page }) => {
	await page.route('**/api/v1/operator/futures-account', (route) =>
		route.fulfill({ status: 503, json: { message: 'Unavailable' } })
	);
	await page.goto('/');
	await expect(page.getByTestId('futures-card')).toContainText(
		'The futures account report is unavailable.'
	);
	await expect(
		page.getByTestId('futures-card').getByRole('button', { name: 'Retry' })
	).toBeVisible();
});
