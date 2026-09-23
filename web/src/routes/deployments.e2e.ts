import { expect, test } from '../e2e/harness';

const deploymentId = '01a0ad72-0000-0000-0000-000000000000';

function deploymentFixture(overrides: Record<string, unknown> = {}) {
	return {
		id: deploymentId,
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
		fills: [],
		...overrides
	};
}

test('deployments is a first-class watch surface with lifecycle-contract gating', async ({
	page
}) => {
	await page.route('**/api/v1/deployments?limit=50&offset=0', (route) => {
		// Paused row without lifecycle contract fields: read-only inventory.
		const {
			lifecycle_command: _lifecycle,
			daily_loss_latched: _daily,
			drawdown_latched: _drawdown,
			revision: _revision,
			worker_lease_held: _lease,
			...partial
		} = deploymentFixture({
			id: '01a0bb90-0000-0000-0000-000000000000',
			product_id: 'UNI-USDC',
			status: 'paused'
		});
		void _lifecycle;
		void _daily;
		void _drawdown;
		void _revision;
		void _lease;
		return route.fulfill({
			json: {
				deployments: [deploymentFixture(), partial],
				limit: 50,
				offset: 0,
				returned: 2
			}
		});
	});
	await page.goto('/deployments');

	// Running deployment with a complete contract is listed and linked to detail.
	const running = page.getByRole('listitem').filter({ hasText: 'UNI-USD' }).first();
	await expect(running).toBeVisible();
	await expect(running.getByText('Lifecycle')).toBeVisible();
	await expect(
		running.getByRole('link', { name: /Open UNI-USD deployment detail/ })
	).toHaveAttribute('href', `/deployments/${deploymentId}`);
	// List rows stay inventory: lifecycle mutations live on the detail page.
	await expect(running.getByRole('button', { name: 'Pause' })).toHaveCount(0);
	await expect(running.getByRole('button', { name: 'Stop…' })).toHaveCount(0);

	// Paused deployment whose payload lacks the lifecycle contract: read-only,
	// no inferred controls.
	const partial = page.getByRole('listitem').filter({ hasText: 'UNI-USDC' });
	await expect(partial).toBeVisible();
	await expect(partial.getByText(/Lifecycle controls are unavailable/)).toBeVisible();

	// Grouping puts Running before Paused.
	const headings = await page.getByRole('heading').allTextContents();
	expect(headings.indexOf('Running')).toBeGreaterThanOrEqual(0);
	expect(headings.indexOf('Paused')).toBeGreaterThan(headings.indexOf('Running'));
});

test('paginates the bounded deployment inventory', async ({ page }) => {
	const fullPage = Array.from({ length: 50 }, (_, index) =>
		deploymentFixture({ id: `01a0ad72-0000-0000-0000-${String(index).padStart(12, '0')}` })
	);
	const secondPage = fullPage.slice(0, 4).map((row, index) => ({
		...row,
		id: `01a0ad90-0000-0000-0000-${String(index).padStart(12, '0')}`
	}));
	await page.route('**/api/v1/deployments?limit=50&offset=0', (route) =>
		route.fulfill({ json: { deployments: fullPage, limit: 50, offset: 0, returned: 50 } })
	);
	await page.route('**/api/v1/deployments?limit=50&offset=50', (route) =>
		route.fulfill({ json: { deployments: secondPage, limit: 50, offset: 50, returned: 4 } })
	);
	await page.goto('/deployments');
	await expect(page.locator('.deploy-card')).toHaveCount(50);
	await expect(page.getByRole('button', { name: 'Next deployment page' })).toBeEnabled();
	await page.getByRole('button', { name: 'Next deployment page' }).click();
	await expect(page.locator('.deploy-card')).toHaveCount(4);
	await expect(
		page.getByRole('button', { name: 'Next deployment page' }),
		'a short page is the end of the inventory'
	).toBeDisabled();
	await page.getByRole('button', { name: 'Previous deployment page' }).click();
	await expect(page.locator('.deploy-card')).toHaveCount(50);
});

test('shows a failed page as a retryable error instead of an empty library', async ({ page }) => {
	await page.route('**/api/v1/deployments?limit=50&offset=0', (route) =>
		route.fulfill({ status: 503, json: { detail: 'Store unavailable' } })
	);
	await page.goto('/deployments');
	await expect(page.getByText("Couldn't load deployments")).toBeVisible();
	await expect(page.getByText('Store unavailable')).toBeVisible();
	await expect(page.getByRole('button', { name: 'Try again' })).toBeVisible();
});

test('an empty trailing page is distinct from an empty inventory', async ({ page }) => {
	const fullPage = Array.from({ length: 50 }, (_, index) =>
		deploymentFixture({ id: `01a0ad72-0000-0000-0000-${String(index).padStart(12, '0')}` })
	);
	await page.route('**/api/v1/deployments?limit=50&offset=0', (route) =>
		route.fulfill({ json: { deployments: fullPage, limit: 50, offset: 0, returned: 50 } })
	);
	// The inventory shrank to exactly one page while the operator was viewing
	// page one; offset 50 now returns zero rows.
	await page.route('**/api/v1/deployments?limit=50&offset=50', (route) =>
		route.fulfill({ json: { deployments: [], limit: 50, offset: 50, returned: 0 } })
	);
	await page.goto('/deployments');
	await expect(page.locator('.deploy-card')).toHaveCount(50);
	await page.getByRole('button', { name: 'Next deployment page' }).click();

	// Not "No deployments yet": the inventory has rows, this page does not.
	await expect(page.getByTestId('trailing-empty-page')).toBeVisible();
	await expect(page.getByText('No deployments yet')).toHaveCount(0);
	await page.getByRole('button', { name: 'Back to first page' }).click();
	await expect(page.locator('.deploy-card')).toHaveCount(50);
	await expect(page.getByTestId('trailing-empty-page')).toHaveCount(0);
});
