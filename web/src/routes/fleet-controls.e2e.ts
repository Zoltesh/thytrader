import { expect, test } from '../e2e/harness';
import { inventoryPageFixture } from '../e2e/inventory';

const inhibition = {
	paper_inhibited: true,
	live_inhibited: true,
	paper_revision: 5,
	live_revision: 9,
	updated_at: '2026-10-06T00:00:00Z'
};

test('live rearm submits the confirmed latch revision only after confirm and live acknowledgement', async ({
	page
}) => {
	await page.route(
		(url) => url.pathname === '/api/v1/deployments',
		(route) => route.fulfill({ json: inventoryPageFixture([]) })
	);
	await page.route(
		(url) => url.pathname === '/api/v1/strategies',
		(route) => route.fulfill({ json: { strategies: [], has_more: false, next_cursor: null } })
	);
	await page.route('**/api/v1/fleet-control', (route) => route.fulfill({ json: inhibition }));
	await page.route('**/api/v1/fleet-control/preview**', (route) =>
		route.fulfill({
			json: {
				action: 'rearm',
				mode: 'live',
				effect: 'Clear latch only',
				cancels_entries: false,
				flattens: false,
				pauses: false,
				requires_live_acknowledgement: true,
				inhibition,
				targets: [],
				as_of: '2026-10-06T00:00:00Z'
			}
		})
	);
	const bodies: Record<string, unknown>[] = [];
	await page.route('**/api/v1/fleet-control/rearm', async (route) => {
		bodies.push(route.request().postDataJSON() as Record<string, unknown>);
		await route.fulfill({
			json: {
				id: 'receipt',
				status: 'completed',
				targets: [],
				note: 'Latch updated; no books resumed.',
				inhibition,
				atomic_venue_transaction: false
			}
		});
	});
	await page.goto('/deployments');
	await page.getByTestId('fleet-mode').selectOption('live');
	await page.getByRole('button', { name: 'Preview Rearm entries' }).click();
	await expect(page.getByTestId('fleet-preview')).toContainText('live 9');
	await page.getByTestId('fleet-open-confirm').click();
	const dialog = page.getByTestId('fleet-dialog');
	const submit = dialog.getByRole('button', { name: 'Confirm fleet action' });
	await expect(submit).toBeDisabled();
	await expect(page.getByTestId('fleet-mode')).toBeDisabled();
	expect(bodies).toEqual([]);
	await dialog.getByRole('checkbox').check();
	await submit.click();
	await expect(page.getByTestId('fleet-result')).toContainText('no books resumed');
	expect(bodies).toHaveLength(1);
	expect(bodies[0]).toMatchObject({
		confirm: true,
		i_understand_live: true,
		mode: 'live',
		expected_inhibition: { live_revision: 9 }
	});
});

test('fleet preview failures are explicit and cannot open a confirmation', async ({ page }) => {
	await page.route(
		(url) => url.pathname === '/api/v1/deployments',
		(route) => route.fulfill({ json: inventoryPageFixture([]) })
	);
	await page.route(
		(url) => url.pathname === '/api/v1/strategies',
		(route) => route.fulfill({ json: { strategies: [], has_more: false, next_cursor: null } })
	);
	await page.route('**/api/v1/fleet-control', (route) => route.fulfill({ json: inhibition }));
	await page.route('**/api/v1/fleet-control/preview**', (route) =>
		route.fulfill({ status: 503, json: { detail: 'Residual state unavailable' } })
	);
	await page.goto('/deployments');
	await page.getByRole('button', { name: 'Preview Flatten' }).click();
	await expect(page.getByTestId('fleet-preview-error')).toHaveText('Residual state unavailable');
	await expect(page.getByTestId('fleet-open-confirm')).toHaveCount(0);
});
