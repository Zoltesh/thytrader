import { type Page } from '@playwright/test';

import { expect, test } from '../e2e/harness';

/**
 * Home fleet entry banner (ADR 0130). Only `GET /api/v1/operator/fleet-health` is mocked;
 * every other Home source reaches the hermetic demo API.
 */

const LEGACY = '01a0f90a-9834-7d0e-afad-ef0b538fee40';
const GAP =
	'order 01a0f93b-9591-7ee1-b037-eae7b079c1b6 FILLED with filled_quantity 0 but fills sum 0.00014174';

function fleetReport(blocked: boolean): Record<string, unknown> {
	return {
		schema_version: 'thytrader-operator-report-v1',
		report_kind: 'fleet_health',
		overall_status: blocked ? 'failed' : 'healthy',
		payload: {
			entries: {
				evaluated_at: '2026-10-10T12:00:00Z',
				complete: true,
				detail: '',
				live_entries_admissible: blocked ? 'blocked' : 'yes',
				paper_entries_admissible: 'yes',
				scopes: [
					{
						mode: 'live',
						scope: 'USDC',
						entries_admissible: blocked ? 'blocked' : 'yes',
						reason_codes: blocked ? ['BREAKER_MARK_MISSING'] : [],
						blocking_deployment_ids: blocked ? [LEGACY] : [],
						running_deployments: 2,
						occupied_deployments: 2,
						alert_subject: 'fleet:live:USDC',
						checks: blocked
							? [
									{
										check: 'daily_loss',
										status: 'blocked',
										reason_code: 'BREAKER_MARK_MISSING',
										blocker_class: 'evidence',
										fleet_wide: true,
										detail: `Daily-loss unavailable for deployment ${LEGACY}.`,
										deployments: [
											{
												deployment_id: LEGACY,
												status: 'stopped',
												product_id: 'BTC-USDC',
												detail: GAP
											}
										]
									}
								]
							: []
					}
				]
			},
			decisions: {
				storage: 'available',
				window_hours: 24,
				since: '2026-10-09T12:00:00Z',
				running_deployments: 2,
				rows_read: 0,
				truncated: false,
				unreadable_deployment_ids: [],
				reasons: [],
				systemic: []
			},
			alert_storage: 'available',
			open_fleet_alerts: []
		}
	};
}

async function openHome(page: Page): Promise<void> {
	await page.goto('/');
	await expect(page.locator('[data-shell-hydrated="true"]')).toHaveCount(1);
	await expect(page.getByRole('heading', { name: 'Home', level: 1 })).toBeVisible();
}

test('shows a fleet-wide entry block with the exact blocking record and bot link', async ({
	page
}) => {
	await page.route('**/api/v1/operator/fleet-health', (route) =>
		route.fulfill({ json: fleetReport(true) })
	);
	await openHome(page);

	const banner = page.getByTestId('fleet-entry-banner');
	await expect(banner).toBeVisible();
	await expect(banner).toHaveAttribute('role', 'alert');
	await expect(banner).toContainText('New entries are blocked fleet-wide');
	await expect(banner).toContainText('Live USDC entries are blocked fleet-wide');
	await expect(banner).toContainText('BREAKER_MARK_MISSING');
	await expect(banner).toContainText(GAP);
	await expect(banner.getByRole('link', { name: 'BTC-USDC (stopped)' })).toHaveAttribute(
		'href',
		`/deployments/${LEGACY}`
	);
});

test('hides the banner while every scope admits entries', async ({ page }) => {
	await page.route('**/api/v1/operator/fleet-health', (route) =>
		route.fulfill({ json: fleetReport(false) })
	);
	await openHome(page);
	await expect(page.getByTestId('kpi-bots')).toBeVisible();
	await expect(page.getByTestId('fleet-entry-banner')).toHaveCount(0);
	await expect(page.getByTestId('fleet-entry-unchecked')).toHaveCount(0);
});

test('says the fleet was not checked when the report fails', async ({ page }) => {
	await page.route('**/api/v1/operator/fleet-health', (route) =>
		route.fulfill({ status: 503, json: { detail: 'unavailable' } })
	);
	await openHome(page);
	await expect(page.getByTestId('fleet-entry-unchecked')).toContainText(
		'Fleet entry health could not be checked (Fleet entry health unavailable (HTTP 503).)'
	);
});
