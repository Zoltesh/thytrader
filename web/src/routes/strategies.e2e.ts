import { expect, test } from '@playwright/test';

const strategyId = '01985cf0-7b60-7000-8000-000000000003';
const fingerprint = `sha256:${'a'.repeat(64)}`;

const draft = {
	schema_version: '1.0',
	strategy_id: strategyId,
	version: 1,
	name: 'Recovered BTC trend draft',
	description: 'Reference research strategy; not trading authority.',
	status: 'draft',
	created_at: '2026-08-14T12:00:00Z',
	instrument: { product_id: 'BTC-USD', base_currency: 'BTC', quote_currency: 'USD' },
	timeframe: '1h',
	data_requirements: {
		warmup_bars: 50,
		required_fields: ['open', 'high', 'low', 'close', 'volume']
	},
	indicators: [],
	entry: { side: 'long', when: { all: [] }, cooldown_bars: 3, max_open_positions: 1 },
	sizing: {
		kind: 'risk_fraction',
		risk_fraction: '0.005',
		min_quote_notional: '10',
		max_quote_notional: '100'
	},
	portfolio_limits: { max_strategy_exposure_fraction: '0.10', max_concurrent_positions: 1 },
	exits: { trailing_stop: { enabled: false } },
	execution: {
		entry_preference: 'maker_only',
		max_entry_wait_bars: 2,
		on_unfilled_entry: 'cancel'
	},
	metadata: { tags: ['reference'], notes: [] }
};

const backtestSummary = {
	initial_equity: '10000',
	final_equity: '10850',
	total_net_pnl: '850',
	total_return_fraction: '0.085',
	gross_profit: '1200',
	gross_loss: '350',
	win_rate: '0.6',
	profit_factor: null,
	average_win: null,
	average_loss: null,
	trade_count: 5,
	winning_trade_count: 3,
	maximum_drawdown: '400',
	maximum_drawdown_fraction: '0.04',
	exposure_bars: 40,
	evaluation_bars: 168
};

const libraryEntry = {
	strategy_id: strategyId,
	name: 'Recovered BTC trend draft',
	product_id: 'BTC-USD',
	timeframe: '1h',
	latest_version: 1,
	status: 'draft',
	latest_fingerprint: null,
	archived: false,
	summary: 'BTC-USD · 1h · EMA(20) crosses above EMA(50) · RSI ≥ 50 · 0.5% risk · $10-$100',
	backtest: null,
	paper_live: { paper: 'unavailable', live: 'unavailable' },
	created_at: '2026-08-14T12:00:00Z',
	updated_at: '2026-08-14T12:00:00Z'
};

const secondStrategyEntry = {
	...libraryEntry,
	strategy_id: '01985cf0-7b60-7000-8000-000000000009'
};

const publishedEntry = {
	...libraryEntry,
	status: 'published',
	latest_fingerprint: fingerprint,
	backtest: {
		result_fingerprint: `sha256:${'b'.repeat(64)}`,
		published_at: '2026-08-20T09:30:00Z',
		summary: backtestSummary
	}
};

function mockLibrary(page: import('@playwright/test').Page, entries: unknown[]): void {
	void page.route('**/api/v1/strategies', async (route) => {
		if (route.request().method() !== 'GET') {
			await route.fulfill({ status: 405, json: { detail: 'method not allowed' } });
			return;
		}
		await route.fulfill({ json: { strategies: entries } });
	});
}

test('shows an empty library with create and import actions when no strategies exist', async ({
	page
}) => {
	mockLibrary(page, []);
	await page.goto('/strategies');
	await expect(page.getByRole('heading', { name: 'Strategy library' })).toBeVisible();
	await expect(page.getByText('No strategies yet.')).toBeVisible();
	await expect(page.getByRole('button', { name: 'New strategy' })).toBeVisible();
	await expect(page.getByRole('button', { name: 'Import JSON…' })).toBeVisible();
});

test('lists every strategy with market, version, status, backtest, and paper/live columns', async ({
	page
}) => {
	mockLibrary(page, [publishedEntry, secondStrategyEntry]);
	await page.goto('/strategies');
	await expect(page.getByRole('cell', { name: 'Recovered BTC trend draft' })).toHaveCount(2);
	await expect(page.getByText('BTC-USD · 1h').first()).toBeVisible();
	const statusPills = page.locator('.status-pill');
	await expect(statusPills).toHaveCount(2);
	await expect(statusPills.first()).toHaveAttribute('data-status', 'published');
	await expect(statusPills.last()).toHaveAttribute('data-status', 'draft');
	await expect(page.getByText('unavailable / unavailable').first()).toBeVisible();
	await expect(page.getByRole('link', { name: /8\.50% · 5 trades/ })).toBeVisible();
});

test('links the latest backtest result to the backtests detail view', async ({ page }) => {
	mockLibrary(page, [publishedEntry]);
	await page.goto('/strategies');
	const backtestLink = page.getByRole('link', { name: /8\.50% · 5 trades/ });
	await expect(backtestLink).toHaveAttribute(
		'href',
		`/backtests?result=${encodeURIComponent(`sha256:${'b'.repeat(64)}`)}`
	);
});

test('creates a reference strategy and refreshes the library', async ({ page }) => {
	mockLibrary(page, [libraryEntry]);
	let createCalls = 0;
	await page.route('**/api/v1/strategies', async (route) => {
		if (route.request().method() === 'POST') {
			createCalls += 1;
			await route.fulfill({ status: 201, json: { strategy: draft, revision: 1 } });
			return;
		}
		await route.fulfill({ json: { strategies: [libraryEntry] } });
	});
	await page.goto('/strategies');
	await page.waitForSelector('table tbody tr');
	await page.getByRole('button', { name: 'New strategy' }).click();
	await expect.poll(() => createCalls).toBe(1);
	await expect(page.getByRole('cell', { name: 'Recovered BTC trend draft' })).toBeVisible();
	await expect(page.getByRole('alert')).not.toBeVisible();
});

test('clones a published strategy by fingerprint and refreshes the library', async ({ page }) => {
	mockLibrary(page, [publishedEntry]);
	let cloneFingerprint = '';
	await page.route('**/api/v1/strategies/clone', async (route) => {
		const body = (await route.request().postDataJSON()) as { strategy_fingerprint: string };
		cloneFingerprint = body.strategy_fingerprint;
		await route.fulfill({ status: 201, json: { strategy: draft, revision: 1 } });
	});
	await page.goto('/strategies');
	await page.waitForSelector('table tbody tr');
	await page.locator('table tbody tr').first().hover();
	await page
		.getByRole('toolbar', { name: 'Row actions' })
		.getByRole('button', { name: 'Clone' })
		.click();
	await expect.poll(() => cloneFingerprint).toBe(fingerprint);
	await expect(page.getByRole('alert')).not.toBeVisible();
});

test('archives a published strategy and refreshes the library', async ({ page }) => {
	const archivedEntry = { ...publishedEntry, status: 'archived', archived: true };
	let archivedOnce = false;
	await page.route('**/api/v1/strategies', async (route) => {
		if (route.request().method() !== 'GET') {
			await route.fulfill({ status: 405, json: { detail: 'method not allowed' } });
			return;
		}
		await route.fulfill({ json: { strategies: [archivedOnce ? archivedEntry : publishedEntry] } });
	});
	await page.route('**/api/v1/strategies/*/archive', async (route) => {
		archivedOnce = true;
		await route.fulfill({
			json: { strategy_fingerprint: fingerprint, archived_at: '2026-08-28T12:00:00Z' }
		});
	});
	await page.goto('/strategies');
	await page.waitForSelector('table tbody tr');
	await page.locator('table tbody tr').first().hover();
	const toolbar = page.getByRole('toolbar', { name: 'Row actions' });
	await toolbar.getByRole('button', { name: 'Archive' }).click();
	await expect(page.locator('.status-pill[data-status="archived"]')).toBeVisible();
	await expect(page.getByRole('alert')).not.toBeVisible();
});

test('shows a hover action bar with status-appropriate actions and edge-safe positioning', async ({
	page
}) => {
	mockLibrary(page, [publishedEntry, secondStrategyEntry]);
	await page.setViewportSize({ width: 900, height: 600 });
	await page.goto('/strategies');
	await page.waitForSelector('table tbody tr');
	await page.waitForTimeout(400);
	await expect(page.locator('th')).toHaveCount(6);
	await expect(page.getByRole('columnheader', { name: 'Actions' })).toHaveCount(0);

	const firstRow = page.locator('table tbody tr').first();
	await firstRow.hover();
	const toolbar = page.getByRole('toolbar', { name: 'Row actions' });
	await expect(toolbar).toBeVisible();
	await expect(toolbar.getByRole('button', { name: 'View' })).toBeVisible();
	await expect(toolbar.getByRole('button', { name: 'Clone' })).toBeVisible();
	await expect(toolbar.getByRole('button', { name: 'Archive' })).toBeVisible();
	await expect(toolbar.getByRole('link', { name: 'Edit' })).toHaveCount(0);
	const barBox = await toolbar.boundingBox();
	expect(barBox).not.toBeNull();
	if (barBox) {
		expect(barBox.x).toBeGreaterThanOrEqual(0);
		expect(barBox.x + barBox.width).toBeLessThanOrEqual(900);
	}

	await page.locator('table tbody tr').nth(1).hover();
	await expect(toolbar.getByRole('link', { name: 'Edit' })).toBeVisible();
	await expect(toolbar.getByRole('button', { name: 'Clone' })).toHaveCount(0);

	await page.locator('tbody').hover({ position: { x: 5, y: 5 } });
	await firstRow.hover();
	await page.mouse.move(10, 10);
	await expect(toolbar).not.toBeVisible();
});

test('imports a pasted strategy definition as a new draft', async ({ page }) => {
	mockLibrary(page, [libraryEntry]);
	let importedBody: unknown = null;
	await page.route('**/api/v1/strategies/import', async (route) => {
		importedBody = await route.request().postDataJSON();
		await route.fulfill({ status: 201, json: { strategy: draft, revision: 1 } });
	});
	await page.goto('/strategies');
	await page.waitForSelector('table tbody tr');
	await page.getByRole('button', { name: 'Import JSON…' }).click();
	await expect(page.getByRole('dialog', { name: 'Import strategy JSON' })).toBeVisible();
	await page.getByLabel('Strategy definition JSON').fill(JSON.stringify(draft));
	await page.getByRole('button', { name: 'Import draft' }).click();
	await expect.poll(() => importedBody).not.toBeNull();
	expect((importedBody as { strategy: { strategy_id: string } }).strategy.strategy_id).toBe(
		strategyId
	);
	await expect(page.getByRole('cell', { name: 'Recovered BTC trend draft' })).toBeVisible();
});

test('rejects invalid import JSON without leaving the dialog or sending a request', async ({
	page
}) => {
	mockLibrary(page, []);
	let importCalls = 0;
	await page.route('**/api/v1/strategies/import', async (route) => {
		importCalls += 1;
		await route.fulfill({ status: 201, json: { strategy: draft, revision: 1 } });
	});
	await page.goto('/strategies');
	await page.waitForSelector('.empty-state');
	await page.getByRole('button', { name: 'Import JSON…' }).click();
	await expect(page.getByRole('dialog', { name: 'Import strategy JSON' })).toBeVisible();
	await page.getByLabel('Strategy definition JSON').fill('{not json');
	await page.getByRole('button', { name: 'Import draft' }).click();
	await expect(page.getByRole('alert').last()).toContainText('not valid JSON');
	await expect(page.getByLabel('Strategy definition JSON')).toBeVisible();
	expect(importCalls).toBe(0);
});

test('surfaces a controlled error banner when the library cannot load', async ({ page }) => {
	await page.route('**/api/v1/strategies', async (route) =>
		route.fulfill({ status: 503, json: { detail: 'Strategy lifecycle storage is unavailable.' } })
	);
	await page.goto('/strategies');
	await expect(page.getByRole('alert')).toContainText('Strategy lifecycle storage is unavailable.');
	await expect(page.getByRole('button', { name: 'Retry library load' })).toBeVisible();
});
