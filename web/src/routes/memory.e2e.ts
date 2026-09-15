import { expect, test } from '../e2e/harness';

test('renders memory status and journals without mutation controls', async ({ page }) => {
	await page.route('**/api/v1/memory**', async (route) => {
		const url = new URL(route.request().url());
		if (route.request().method() !== 'GET') {
			await route.fulfill({ status: 405, json: { detail: 'read-only' } });
			return;
		}
		if (url.pathname === '/api/v1/memory') {
			await route.fulfill({
				json: {
					schema_version: 'thytrader-experiential-memory-v1',
					counts: { journals: 1, sentiment: 0, patterns: 0, notifications: 0 },
					notify_provider: 'none',
					notify_webhook_configured: false,
					notify_enabled: false,
					storage: 'available'
				}
			});
			return;
		}
		if (url.pathname === '/api/v1/memory/monitor') {
			await route.fulfill({
				json: {
					schema_version: 'thytrader-monitor-v1',
					memory: {
						schema_version: 'thytrader-experiential-memory-v1',
						counts: { journals: 1, sentiment: 0, patterns: 0, notifications: 0 },
						notify_provider: 'none',
						notify_webhook_configured: false,
						notify_enabled: false,
						storage: 'available'
					},
					deployments: [],
					recent_journals: [],
					recent_notifications: [],
					findings: []
				}
			});
			return;
		}
		if (url.pathname === '/api/v1/memory/journals') {
			await route.fulfill({
				json: {
					journals: [
						{
							id: '01985cf0-7b60-7000-8000-000000000011',
							occurred_at: '2026-09-15T12:00:00Z',
							origin: 'human',
							kind: 'note',
							title: 'Paper pause was correct',
							body: 'Paused after stale candles.',
							product_id: 'BTC-USD',
							runtime_mode: 'paper',
							lesson_outcome: 'none'
						}
					]
				}
			});
			return;
		}
		await route.fulfill({ json: { sentiment: [], patterns: [], notifications: [], journals: [] } });
	});

	await page.goto('/memory');
	await expect(page.getByRole('heading', { name: 'Journals and monitor' })).toBeVisible();
	await expect(page.getByTestId('memory-status')).toContainText('none');
	await expect(page.getByText('Paper pause was correct')).toBeVisible();
	await expect(page.getByRole('button', { name: /journal/i })).toHaveCount(0);
});
