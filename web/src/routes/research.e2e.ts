import { expect, test } from '../e2e/harness';

test('research is a first-class workstation page with a strategy picker', async ({ page }) => {
	await page.route('**/api/v1/strategies**', async (route) => {
		await route.fulfill({ json: { strategies: [] } });
	});
	await page.goto('/research');
	await expect(page.getByRole('heading', { name: 'Research' })).toBeVisible();
	await expect(page.getByText('Choose a strategy to launch research')).toBeVisible();
	const nav = page.getByRole('navigation', { name: 'Primary navigation' });
	await expect(nav.getByRole('link', { name: 'Research' })).toHaveAttribute('href', '/research');
	await expect(nav.getByRole('link', { name: 'Chat' })).toHaveAttribute('href', '/chat');
});
