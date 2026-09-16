import { expect, test } from '../e2e/harness';

test('journals lists origin-attributed memory rows without mutation controls', async ({ page }) => {
	await page.route('**/api/v1/memory/journals**', async (route) => {
		if (route.request().method() !== 'GET') {
			await route.fulfill({ status: 405, json: { detail: 'read-only' } });
			return;
		}
		await route.fulfill({
			json: {
				journals: [
					{
						id: '01985cf0-7b60-7000-8000-000000000021',
						occurred_at: '2026-09-15T12:00:00Z',
						origin: 'human',
						kind: 'note',
						title: 'Visible journal place',
						body: 'Not a per-trade why record.',
						product_id: 'BTC-USD',
						runtime_mode: 'paper',
						lesson_outcome: 'none'
					}
				]
			}
		});
	});

	await page.goto('/journals');
	await expect(page.getByRole('heading', { name: 'Journals' })).toBeVisible();
	await expect(page.getByText('Visible journal place')).toBeVisible();
	await expect(page.getByRole('link', { name: 'Memory' })).toHaveAttribute('href', '/memory');
	await expect(page.getByRole('link', { name: 'Trade' })).toHaveAttribute('href', '/trade');
	await expect(page.getByRole('button', { name: /add journal/i })).toHaveCount(0);
	const nav = page.getByRole('navigation', { name: 'Primary navigation' });
	await expect(nav.getByRole('link', { name: 'Journals' })).toHaveAttribute('href', '/journals');
	await expect(nav.getByRole('link', { name: 'Chat' })).toHaveAttribute('href', '/chat');
});
