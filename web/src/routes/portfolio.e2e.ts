import { expect, test } from '@playwright/test';

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

test('shows a practical demo portfolio and detected extra permissions', async ({ page }) => {
	await page.route('**/api/v1/portfolio', async (route) => {
		await route.fulfill({ json: demoPortfolio });
	});

	await page.goto('/');

	await expect(page.getByRole('heading', { name: 'Your portfolio' })).toBeVisible();
	await expect(page.getByText('Demo data')).toBeVisible();
	await expect(page.getByText('$98,542.17')).toBeVisible();
	await expect(page.getByRole('row', { name: /Bitcoin BTC/ })).toContainText('0.76000000');
	await expect(page.getByRole('row', { name: /Ethereum ETH/ })).toContainText('$7,342.17');
	await expect(page.getByText('View', { exact: true })).toBeVisible();
	await expect(page.getByText('Trade', { exact: true })).toBeVisible();
	await expect(page.getByText('Transfer', { exact: true })).toBeVisible();
});

test('loads demo portfolio through the real SvelteKit and FastAPI processes', async ({ page }) => {
	await page.goto('/');

	await expect(page.getByText('Demo data')).toBeVisible();
	await expect(page.getByText('$99,792.17')).toBeVisible();
	await expect(page.getByRole('row', { name: /Bitcoin BTC/ })).toBeVisible();
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
