import { expect, test } from '@playwright/test';

const sampleAuditEvents = {
	events: [
		{
			id: '01985cf0-7b60-7000-8000-000000000001',
			occurred_at: '2026-08-17T12:00:00Z',
			category: 'snapshot',
			action: 'portfolio_snapshot_recorded',
			outcome: 'success',
			detail: 'Snapshot recorded total_value=98542.17 USD',
			provider: 'coinbase',
			product_id: null,
			recorded_at: '2026-08-17T12:00:01Z'
		},
		{
			id: '01985cf0-7b60-7000-8000-000000000002',
			occurred_at: '2026-08-17T11:55:00Z',
			category: 'connection',
			action: 'worker_started',
			outcome: 'info',
			detail: 'Portfolio worker started successfully',
			provider: null,
			product_id: null,
			recorded_at: '2026-08-17T11:55:01Z'
		}
	]
};

test('renders audit events list with categories, outcomes, and details', async ({ page }) => {
	await page.route('**/api/v1/audit-events*', async (route) => {
		await route.fulfill({ json: sampleAuditEvents });
	});

	await page.goto('/audit');

	await expect(page.getByRole('heading', { name: 'Audit trail' })).toBeVisible();
	await expect(page.getByText('portfolio_snapshot_recorded')).toBeVisible();
	await expect(page.getByText('worker_started')).toBeVisible();
	await expect(page.getByText('Snapshot recorded total_value=98542.17 USD')).toBeVisible();
	await expect(page.getByText('2026-08-17 12:00:00 UTC', { exact: true })).toBeVisible();
});

test('renders empty state when no audit events exist', async ({ page }) => {
	await page.route('**/api/v1/audit-events*', async (route) => {
		await route.fulfill({ json: { events: [] } });
	});

	await page.goto('/audit');

	await expect(page.getByRole('heading', { name: 'Audit trail' })).toBeVisible();
	await expect(page.getByText('No audit events recorded')).toBeVisible();
});

test('presents controlled unavailable state when audit storage fails with 503', async ({
	page
}) => {
	await page.route('**/api/v1/audit-events*', async (route) => {
		await route.fulfill({
			status: 503,
			json: {
				detail: {
					code: 'persistence_unavailable',
					message: 'Audit event storage is unavailable.'
				}
			}
		});
	});

	await page.goto('/audit');

	await expect(page.getByRole('alert')).toContainText('Audit event storage is unavailable.');
	await expect(page.getByRole('button', { name: 'Try again' })).toBeVisible();
});
