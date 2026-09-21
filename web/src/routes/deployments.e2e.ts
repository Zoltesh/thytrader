import { expect, test } from '../e2e/harness';

test('deployments is a first-class watch surface with lifecycle-contract gating', async ({
	page
}) => {
	await page.route('**/api/v1/deployments', async (route) => {
		await route.fulfill({
			json: {
				deployments: [
					{
						id: '01a0ad72-0000-0000-0000-000000000000',
						strategy_fingerprint: 'sha256:abc',
						strategy_id: '01a0ad42-0000-0000-0000-000000000000',
						kind: 'strategy',
						timeframe: '1h',
						product_id: 'UNI-USD',
						mode: 'paper',
						status: 'running',
						phase: 'flat',
						cash: '10000',
						paper_starting_cash: '10000',
						last_evaluated_bar: '2026-09-21T20:00:00+00:00',
						last_signal: null,
						mismatch_detail: null,
						pending_entry_bars: 0,
						bars_held: 0,
						lifecycle_command: 'none',
						daily_loss_latched: false,
						drawdown_latched: false,
						revision: 12,
						worker_lease_held: true,
						created_at: '2026-09-20T00:00:00+00:00',
						updated_at: '2026-09-21T20:00:00+00:00',
						position: null,
						positions: [],
						instrument_runtimes: [],
						orders: [],
						fills: []
					},
					{
						id: '01a0bb90-0000-0000-0000-000000000000',
						strategy_fingerprint: 'sha256:def',
						strategy_id: '01a0b850-0000-0000-0000-000000000000',
						kind: 'strategy',
						timeframe: '1h',
						product_id: 'UNI-USDC',
						mode: 'paper',
						status: 'paused',
						phase: 'pending_entry',
						cash: '10000',
						paper_starting_cash: '10000',
						last_evaluated_bar: '2026-09-21T19:00:00+00:00',
						last_signal: 'matched',
						mismatch_detail: null,
						pending_entry_bars: 0,
						bars_held: 0,
						position: null,
						positions: [],
						instrument_runtimes: [],
						orders: [],
						fills: []
					}
				],
				limit: 50,
				offset: 0,
				returned: 2
			}
		});
	});
	await page.goto('/deployments');

	// Running deployment with a complete contract: lifecycle actions visible.
	const running = page.getByRole('article').filter({ hasText: 'UNI-USD' }).first();
	await expect(running).toBeVisible();
	await expect(running.getByText('Lifecycle')).toBeVisible();
	await expect(running.getByRole('button', { name: 'Pause' })).toBeVisible();
	await expect(running.getByRole('button', { name: 'Stop…' })).toBeVisible();

	// Paused deployment whose payload lacks the lifecycle contract: read-only,
	// no inferred controls.
	const partial = page.getByRole('article').filter({ hasText: 'UNI-USDC' });
	await expect(partial).toBeVisible();
	await expect(partial.getByText(/Lifecycle controls are unavailable/)).toBeVisible();
	await expect(partial.getByRole('button', { name: 'Resume' })).toBeHidden();

	// Grouping puts Running before Paused.
	const headings = await page.getByRole('heading').allTextContents();
	expect(headings.indexOf('Running')).toBeGreaterThanOrEqual(0);
	expect(headings.indexOf('Paused')).toBeGreaterThan(headings.indexOf('Running'));
});
