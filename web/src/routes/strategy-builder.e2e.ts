import { expect, test } from '@playwright/test';

const strategyId = '01985cf0-7b60-7000-8000-000000000007';
const fingerprint = `sha256:${'c'.repeat(64)}`;

const draft = {
	schema_version: '1.0',
	strategy_id: strategyId,
	version: 1,
	name: 'Builder test trend',
	description: 'Reference research strategy; not trading authority.',
	status: 'draft',
	created_at: '2026-08-14T12:00:00Z',
	instrument: { product_id: 'BTC-USD', base_currency: 'BTC', quote_currency: 'USD' },
	timeframe: '1h',
	data_requirements: {
		warmup_bars: 50,
		required_fields: ['open', 'high', 'low', 'close', 'volume']
	},
	indicators: [
		{ id: 'fast', kind: 'ema', input: 'close', parameters: { period: 20 } },
		{ id: 'slow', kind: 'ema', input: 'close', parameters: { period: 50 } },
		{ id: 'rsi', kind: 'rsi', input: 'close', parameters: { period: 14 } }
	],
	entry: {
		side: 'long',
		when: {
			all: [
				{
					left: { indicator: 'fast' },
					operator: 'crosses_above',
					right: { indicator: 'slow' }
				},
				{
					left: { indicator: 'rsi' },
					operator: 'greater_than_or_equal',
					right: { literal: '50' }
				}
			]
		},
		cooldown_bars: 3,
		max_open_positions: 1
	},
	sizing: {
		kind: 'risk_fraction',
		risk_fraction: '0.005',
		min_quote_notional: '10',
		max_quote_notional: '100'
	},
	portfolio_limits: { max_strategy_exposure_fraction: '0.10', max_concurrent_positions: 1 },
	exits: {
		initial_stop: { kind: 'atr_multiple', atr_indicator: 'atr', multiple: '2' },
		take_profit: { kind: 'reward_risk', multiple: '2' },
		trailing_stop: { enabled: false },
		time_exit: { max_bars_held: 96 }
	},
	execution: {
		entry_preference: 'maker_only',
		max_entry_wait_bars: 2,
		on_unfilled_entry: 'cancel'
	},
	metadata: { tags: ['reference'], notes: [] }
};

const libraryEntry = {
	strategy_id: strategyId,
	name: 'Builder test trend',
	product_id: 'BTC-USD',
	timeframe: '1h',
	latest_version: 1,
	status: 'draft',
	latest_fingerprint: null,
	archived: false,
	summary: 'BTC-USD · 1h · EMA(20) crosses above EMA(50) · RSI ≥ 50 · 0.5% risk · $10-$100',
	backtest: null,
	paper_live: { paper: 'unavailable', live: 'unavailable' },
	created_at: draft.created_at,
	updated_at: draft.created_at
};

function mockDraftStorage(page: import('@playwright/test').Page): void {
	void page.route(`**/api/v1/strategies/${strategyId}/versions/1`, async (route) =>
		route.fulfill({ json: { strategy: draft, revision: 1 } })
	);
	void page.route('**/api/v1/strategies', async (route) => {
		if (route.request().method() !== 'GET') {
			await route.fulfill({ status: 405, json: { detail: 'method not allowed' } });
			return;
		}
		await route.fulfill({ json: { strategies: [libraryEntry] } });
	});
}

test('loads a draft into the builder with sections, rule tree, and inspector summary', async ({
	page
}) => {
	mockDraftStorage(page);
	await page.goto(`/strategies/${strategyId}`);
	await expect(page.getByRole('heading', { name: 'Builder test trend' })).toBeVisible();
	await expect(page.getByRole('button', { name: 'Indicators' })).toBeVisible();
	await page.getByRole('button', { name: 'Entry conditions' }).click();
	await expect(page.getByText('ALL', { exact: true })).toBeVisible();
	const summary = page.locator('.inspector-block').first();
	await expect(summary).toContainText('fast crosses above slow');
	await expect(summary).toContainText('rsi ≥ 50');
	await expect(page.getByRole('button', { name: 'Save draft' })).toBeEnabled();
});

test('marks unsaved changes and blocks saving when validation fails', async ({ page }) => {
	mockDraftStorage(page);
	await page.goto(`/strategies/${strategyId}`);
	await page.getByLabel('Strategy name').fill('');
	await expect(page.locator('.dirty-pill')).toBeVisible();
	await expect(page.locator('.problems')).toContainText('Name is required.');
	await expect(page.getByRole('button', { name: 'Save draft' })).toBeDisabled();
});

test('flags engine settings the current backtester does not model', async ({ page }) => {
	mockDraftStorage(page);
	await page.goto(`/strategies/${strategyId}`);
	const engineList = page.locator('.engine-list');
	await expect(engineList).toContainText('Entry cooldown (cooldown_bars)');
	await expect(engineList).toContainText('not modeled by the current backtester');
	await expect(engineList).toContainText('Maker-only / marketable entry preference');
	const unsupported = engineList.locator('li.unsupported');
	await expect(unsupported).toHaveCount(4);
});

test('saves edited builder state through the durable draft boundary', async ({ page }) => {
	type SavedPayload = { strategy: { name: string }; revision: number };
	let savedBody = null as SavedPayload | null;
	let savedOnce = false;
	await page.route(`**/api/v1/strategies/${strategyId}/versions/1`, async (route) => {
		if (route.request().method() === 'PUT') {
			savedBody = (await route.request().postDataJSON()) as SavedPayload;
			savedOnce = true;
			const strategy = { ...(draft as object), name: savedBody.strategy.name } as typeof draft;
			await route.fulfill({ json: { strategy, revision: 2 } });
			return;
		}
		await route.fulfill({ json: { strategy: draft, revision: 1 } });
	});
	await page.route('**/api/v1/strategies', async (route) =>
		route.fulfill({ json: { strategies: [libraryEntry] } })
	);
	await page.goto(`/strategies/${strategyId}`);
	await page.getByLabel('Strategy name').fill('Renamed in builder');
	await page.getByRole('button', { name: 'Save draft' }).click();
	await expect.poll(() => savedOnce).toBe(true);
	const saved = savedBody as SavedPayload;
	expect(saved.strategy.name).toBe('Renamed in builder');
	expect(saved.revision).toBe(1);
	await expect(page.getByText('All edits saved.')).toBeVisible();
});

test('refuses to open a builder for a published or archived identity', async ({ page }) => {
	void page.route(`**/api/v1/strategies/${strategyId}/versions/1`, async (route) =>
		route.fulfill({ json: { strategy: draft, revision: 1 } })
	);
	void page.route('**/api/v1/strategies', async (route) =>
		route.fulfill({
			json: {
				strategies: [{ ...libraryEntry, status: 'published', latest_fingerprint: fingerprint }]
			}
		})
	);
	await page.goto(`/strategies/${strategyId}`);
	await expect(page.getByRole('alert')).toContainText(
		'published or archived; its builder is read-only history'
	);
});
