import { expect, test } from '../e2e/harness';

test('deploy folds under Strategies and keeps its strategy picker', async ({ page }) => {
	await page.route('**/api/v1/strategies**', async (route) => {
		await route.fulfill({ json: { strategies: [] } });
	});
	await page.goto('/deploy');
	await expect(page.getByRole('heading', { name: 'Deploy' })).toBeVisible();
	await expect(page.getByText('Loading the strategy library…')).toBeVisible();
	await expect(page.getByText('No strategies yet')).toBeVisible();
	const nav = page.getByRole('navigation', { name: 'Primary navigation' });
	await expect(nav.getByRole('link', { name: 'Strategies' })).toHaveAttribute(
		'aria-current',
		'page'
	);
	await expect(page.getByTestId('breadcrumb')).toHaveText(/Strategies\s*\/\s*Deploy/);
});
