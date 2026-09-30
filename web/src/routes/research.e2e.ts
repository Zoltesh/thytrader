import { expect, test } from '../e2e/harness';

test('research folds under Strategies and keeps its strategy picker', async ({ page }) => {
	await page.route('**/api/v1/strategies**', async (route) => {
		await route.fulfill({ json: { strategies: [] } });
	});
	await page.goto('/research');
	await expect(page.getByRole('heading', { name: 'Research' })).toBeVisible();
	await expect(page.getByText('Choose a strategy to launch research')).toBeVisible();
	const nav = page.getByRole('navigation', { name: 'Primary navigation' });
	await expect(nav.getByRole('link', { name: 'Strategies' })).toHaveAttribute(
		'aria-current',
		'page'
	);
	await expect(page.getByTestId('breadcrumb')).toHaveText(/Strategies\s*\/\s*Research/);
});
