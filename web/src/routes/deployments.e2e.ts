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

const strategyId = '01a0ad42-0000-0000-0000-000000000000';
const liveId = '01a0ad73-0000-0000-0000-000000000000';

async function mockInventory(page: import('@playwright/test').Page, rows: unknown[]) {
	await page.route(
		(url) => url.pathname === '/api/v1/deployments',
		(route) => {
			const url = new URL(route.request().url());
			const limit = Number(url.searchParams.get('limit') ?? '50');
			const offset = Number(url.searchParams.get('offset') ?? '0');
			const slice = rows.slice(offset, offset + limit);
			return route.fulfill({
				json: { deployments: slice, limit, offset, returned: slice.length }
			});
		}
	);
	await page.route(
		(url) => url.pathname === '/api/v1/strategies',
		(route) =>
			route.fulfill({
				json: {
					strategies: [
						{
							strategy_id: strategyId,
							name: 'UNI trend',
							product_id: 'UNI-USDC',
							timeframe: '1h',
							revision: 2,
							valid: true,
							current_fingerprint: 'sha256:abc',
							summary: '',
							backtest: null,
							paper_live: { paper: 'running', live: 'running' },
							active_deployment_count: 2,
							created_at: '2026-09-01T00:00:00Z',
							updated_at: '2026-09-01T00:00:00Z'
						}
					],
					has_more: false,
					next_cursor: null
				}
			})
	);
}

test('Portfolio groups bots, filters by mode, and keeps lifecycle contract gating', async ({
	page
}) => {
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
	await mockInventory(page, [
		deploymentFixture({
			ledger: {
				trade_count: 2,
				total_net_pnl: '18.4',
				total_return_fraction: '0.0018',
				mark_complete: true,
				marked_exposure: null
			},
			capital: { allocated_capital: '1000', performance_equity: '1018.4' }
		}),
		deploymentFixture({
			id: liveId,
			mode: 'live',
			product_id: 'ETH-USDC',
			strategy_fingerprint: 'sha256:other',
			capital: { allocated_capital: '100' }
		}),
		partial,
		deploymentFixture({ id: '01a0bb91-0000-0000-0000-000000000000', status: 'reconciling' }),
		deploymentFixture({ id: '01a0bb92-0000-0000-0000-000000000000', status: 'stopped' })
	]);
	await page.goto('/deployments');

	await expect(page.getByRole('heading', { level: 1, name: 'Portfolio' })).toBeVisible();
	await expect(page.getByTestId('breadcrumb')).toHaveText('Portfolio');
	await expect(page.getByTestId('bot-row')).toHaveCount(5);
	// Groups in order: Needs attention, Running, Paused, Stopped.
	const groups = await page
		.locator('[data-group]')
		.evaluateAll((nodes) => nodes.map((node) => node.getAttribute('data-group')));
	expect(groups).toEqual(['attention', 'running', 'paused', 'stopped']);

	// Rows: name + version from the library by exact fingerprint, market with its own quote.
	const running = page.locator('[data-group="running"]').getByTestId('bot-row');
	await expect(running).toHaveCount(2);
	const paperRow = running.filter({ hasText: 'UNI / USD' });
	await expect(paperRow.first()).toContainText('Current rules');
	await expect(paperRow).toContainText('UNI / USD');
	await expect(paperRow).toContainText('+18.40 USD');
	await expect(paperRow.getByRole('link')).toHaveAttribute('href', `/deployments/${deploymentId}`);
	const liveRow = running.filter({ hasText: 'ETH / USDC' });
	await expect(liveRow.locator('.chip.live')).toHaveText('LIVE');
	// A bot on other rules of the same strategy says "Earlier edit", never a version number.
	await expect(liveRow).toContainText('UNI trend');
	await expect(liveRow).toContainText('Earlier edit');
	// List rows stay inventory: lifecycle mutations live on the detail page.
	await expect(page.getByRole('button', { name: /Pause|Stop…|Resume/ })).toHaveCount(0);

	// Paused deployment whose payload lacks the lifecycle contract: read-only, no inferred controls.
	await expect(page.locator('[data-group="paused"]')).toContainText(
		'Read-only: lifecycle contract incomplete'
	);

	// Header metrics: counts over the whole inventory; money never mixes paper and live.
	await expect(page.getByTestId('metric-running')).toHaveText('2');
	await expect(page.getByTestId('metric-paused')).toHaveText('1');
	await expect(page.getByTestId('metric-attention')).toHaveText('1');
	await expect(page.getByTestId('metric-allocated')).toHaveText('—');
	await expect(page.getByTestId('portfolio-metrics')).toContainText(
		'never totalled with real money'
	);
	await expect(page.getByTestId('portfolio-coming')).toContainText(
		'Deploying a portfolio arrives next'
	);
	// Portfolio is not live exposure by itself.
	await expect(page.getByTestId('live-strip')).toHaveCount(0);

	// Live filter: only the live row, and money totals for live alone.
	const filter = page.getByTestId('portfolio-filter');
	await filter.getByRole('button', { name: /Live/ }).click();
	await expect(filter.getByRole('button', { name: /Live/ })).toHaveAttribute(
		'aria-pressed',
		'true'
	);
	await expect(page.getByTestId('bot-row')).toHaveCount(1);
	await expect(page.getByTestId('bot-row')).toHaveAttribute('data-mode', 'live');
	await expect(page.getByTestId('metric-allocated')).toHaveText('100.00 USDC');
	await expect(page.getByTestId('metric-running')).toHaveText('1');

	// Paper filter: the paper rows, with an exact paper total.
	await filter.getByRole('button', { name: /Paper/ }).click();
	await expect(page.locator('[data-mode="live"]')).toHaveCount(0);
	await expect(page.getByTestId('metric-allocated')).toHaveText('1,000.00 USD');

	// Start a deployment goes to the strategy library, then a strategy's Run stage.
	await expect(page.getByRole('link', { name: 'Start a deployment' })).toHaveAttribute(
		'href',
		'/strategies'
	);
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
	await expect(page.getByTestId('bot-row')).toHaveCount(50);
	await expect(page.getByRole('button', { name: 'Next deployment page' })).toBeEnabled();
	await page.getByRole('button', { name: 'Next deployment page' }).click();
	await expect(page.getByTestId('bot-row')).toHaveCount(4);
	await expect(
		page.getByRole('button', { name: 'Next deployment page' }),
		'a short page is the end of the inventory'
	).toBeDisabled();
	await page.getByRole('button', { name: 'Previous deployment page' }).click();
	await expect(page.getByTestId('bot-row')).toHaveCount(50);
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
	await expect(page.getByTestId('bot-row')).toHaveCount(50);
	await page.getByRole('button', { name: 'Next deployment page' }).click();

	// Not "No bots yet": the inventory has rows, this page does not.
	await expect(page.getByTestId('trailing-empty-page')).toBeVisible();
	await expect(page.getByText('No bots yet')).toHaveCount(0);
	await page.getByRole('button', { name: 'Back to first page' }).click();
	await expect(page.getByTestId('bot-row')).toHaveCount(50);
	await expect(page.getByTestId('trailing-empty-page')).toHaveCount(0);
});

test('Portfolio labels a kept live bot of a deleted strategy', async ({ page }) => {
	await mockInventory(page, [
		deploymentFixture({
			id: liveId,
			mode: 'live',
			status: 'stopped',
			strategy_id: null,
			strategy_deleted: true,
			strategy_name: 'Old breakout',
			strategy_fingerprint: 'sha256:gone',
			paper_starting_cash: null
		})
	]);
	await page.goto('/deployments');
	const row = page.locator('[data-group="stopped"]').getByTestId('bot-row');
	await expect(row).toContainText('Old breakout (deleted strategy)');
	await expect(row).not.toContainText('Current rules');
	await expect(row).not.toContainText('Earlier edit');
});
