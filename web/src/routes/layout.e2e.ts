import { expect, test } from '@playwright/test';

test('shared topbar marks the active route and stays reachable under 800px', async ({ page }) => {
	await page.route('**/api/v1/portfolio', async (route) => {
		await route.fulfill({
			json: {
				as_of: '2026-07-27T22:15:00Z',
				connection: { provider: 'coinbase', status: 'demo', permissions: ['view'] },
				demo: true,
				total_value: { amount: '1', currency: 'USD' },
				assets: [],
				unvalued_assets: []
			}
		});
	});
	await page.route('**/api/v1/strategies', async (route) => {
		await route.fulfill({ json: { strategies: [] } });
	});

	await page.setViewportSize({ width: 700, height: 800 });
	await page.goto('/');

	const nav = page.getByRole('navigation', { name: 'Primary navigation' });
	await expect(nav).toHaveCount(1);
	await expect(nav.getByRole('link', { name: 'Portfolio' })).toBeVisible();
	await expect(nav.getByRole('link', { name: 'Strategies' })).toBeVisible();
	await expect(nav.getByRole('link', { name: 'Backtests' })).toBeVisible();
	await expect(nav.getByRole('link', { name: 'Audit' })).toBeVisible();
	await expect(nav.getByRole('link', { name: 'Portfolio' })).toHaveAttribute(
		'aria-current',
		'page'
	);
	await expect(page.getByText('Local workstation')).toBeVisible();

	await nav.getByRole('link', { name: 'Strategies' }).click();
	await expect(page).toHaveURL(/\/strategies\/?$/);
	await expect(nav.getByRole('link', { name: 'Strategies' })).toHaveAttribute(
		'aria-current',
		'page'
	);
	await expect(nav.getByRole('link', { name: 'Portfolio' })).not.toHaveAttribute(
		'aria-current',
		'page'
	);
	await expect(page.getByText('Research only')).toBeVisible();
	await expect(page.getByText('Local workstation')).toHaveCount(0);
});

test('strategy builder keeps Strategies active and the research pill', async ({ page }) => {
	await page.route('**/api/v1/strategies**', async (route) =>
		route.fulfill({ json: { strategies: [] } })
	);
	await page.goto('/strategies/01985cf0-7b60-7000-8000-000000000007');

	const nav = page.getByRole('navigation', { name: 'Primary navigation' });
	await expect(nav.getByRole('link', { name: 'Strategies' })).toHaveAttribute(
		'aria-current',
		'page'
	);
	await expect(page.getByText('Research only')).toBeVisible();
});
