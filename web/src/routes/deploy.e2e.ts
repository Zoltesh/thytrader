import { expect, test } from '../e2e/harness';

test('deploy is a first-class workstation page with a strategy picker', async ({ page }) => {
	await page.route('**/api/v1/strategies**', async (route) => {
		await route.fulfill({ json: { strategies: [] } });
	});
	await page.goto('/deploy');
	await expect(page.getByRole('heading', { name: 'Deploy' })).toBeVisible();
	await expect(page.getByText('Loading the strategy library…')).toBeVisible();
	await expect(page.getByText('No strategies yet')).toBeVisible();
	const nav = page.getByRole('navigation', { name: 'Primary navigation' });
	await expect(nav.getByRole('link', { name: 'Deploy', exact: true })).toHaveAttribute(
		'href',
		'/deploy'
	);
	await expect(nav.getByRole('link', { name: 'Chat' })).toHaveAttribute('href', '/chat');
});
