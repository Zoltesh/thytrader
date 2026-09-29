import { expect, isStrategyLibraryRequest, test } from '../e2e/harness';

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
		{ id: 'rsi', kind: 'rsi', input: 'close', parameters: { period: 14 } },
		{ id: 'atr', kind: 'atr', input: ['high', 'low', 'close'], parameters: { period: 14 } }
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
	summary: 'BTC-USD · 1h · EMA(20) crosses above EMA(50) AND RSI(14) ≥ 50 · 0.5% risk · $10-$100',
	backtest: null,
	paper_live: { paper: 'unavailable', live: 'unavailable' },
	created_at: draft.created_at,
	updated_at: draft.created_at
};

async function mockDraftStorage(page: import('@playwright/test').Page): Promise<void> {
	await page.route(`**/api/v1/strategies/${strategyId}/history`, async (route) => {
		await route.fulfill({
			json: {
				strategy_id: strategyId,
				latest_version: 1,
				next_version: 2,
				versions: [],
				draft: { strategy: draft, revision: 1 }
			}
		});
	});
	await page.route(`**/api/v1/strategies/${strategyId}/versions/1`, async (route) => {
		await route.fulfill({ json: { strategy: draft, revision: 1 } });
	});
	await page.route(isStrategyLibraryRequest, async (route) => {
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
	await mockDraftStorage(page);
	await page.goto(`/strategies/${strategyId}`);
	await expect(page.getByRole('heading', { name: 'Builder test trend' })).toBeVisible();
	await expect(page.getByRole('button', { name: 'Indicators' })).toBeVisible();
	await page.getByRole('button', { name: 'Entry conditions' }).click();
	await expect(page.getByText('ALL', { exact: true })).toBeVisible();
	const summary = page.locator('.inspector-block').first();
	await expect(summary).toContainText('fast crosses above slow');
	await expect(summary).toContainText('rsi ≥ 50');
	await expect(
		page.getByText('50 completed 1h bars (OHLCV) before the first signal.')
	).toBeVisible();
	await expect(page.getByRole('button', { name: 'Save draft' })).toBeEnabled();
});

test('marks unsaved changes and blocks saving when validation fails', async ({ page }) => {
	await mockDraftStorage(page);
	await page.goto(`/strategies/${strategyId}`);
	await page.getByLabel('Strategy name').fill('');
	await expect(page.locator('.dirty-pill')).toBeVisible();
	await expect(page.locator('.problems')).toContainText('Name is required.');
	await expect(page.getByRole('button', { name: 'Save draft' })).toBeDisabled();
});

test('flags engine settings the current backtester does not model', async ({ page }) => {
	await mockDraftStorage(page);
	await page.goto(`/strategies/${strategyId}`);
	const engineMatrix = page.getByRole('table', { name: 'Engine support matrix' });
	await expect(engineMatrix.getByRole('columnheader', { name: 'V1' })).toBeVisible();
	await expect(engineMatrix.getByRole('columnheader', { name: 'V2' })).toBeVisible();
	await expect(engineMatrix.getByRole('columnheader', { name: 'V3' })).toBeVisible();
	await expect(engineMatrix.getByRole('columnheader', { name: 'V4' })).toBeVisible();
	const cooldown = engineMatrix.getByRole('row', { name: /Entry cooldown/ });
	await expect(cooldown).toContainText('V3/V4 maker path blocks re-entry');
	await expect(cooldown.getByText('Unsupported')).toHaveCount(2);
	const makerEntry = engineMatrix.getByRole('row', { name: /Maker-only/ });
	await expect(makerEntry.getByText('Unsupported', { exact: true })).toHaveCount(2);
	await expect(makerEntry.getByText('Supported', { exact: true })).toHaveCount(2);
});

test('saves edited builder state through the durable draft boundary', async ({ page }) => {
	type SavedPayload = { strategy: { name: string }; revision: number };
	let savedBody = null as SavedPayload | null;
	let savedOnce = false;
	await page.route(`**/api/v1/strategies/${strategyId}/history`, async (route) => {
		await route.fulfill({
			json: {
				strategy_id: strategyId,
				latest_version: 1,
				next_version: 2,
				versions: [],
				draft: { strategy: draft, revision: 1 }
			}
		});
	});
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
	await page.route(isStrategyLibraryRequest, async (route) =>
		route.fulfill({ json: { strategies: [libraryEntry] } })
	);
	await page.goto(`/strategies/${strategyId}`);
	await page.getByLabel('Strategy name').fill('Renamed in builder');
	await page.getByRole('button', { name: 'Save draft' }).click();
	await expect.poll(() => savedOnce).toBe(true);
	const saved = savedBody as SavedPayload;
	expect(saved.strategy.name).toBe('Renamed in builder');
	expect(saved.revision).toBe(1);
	await expect(page.getByText(`Saved ${new Date().toLocaleTimeString()}`)).toBeVisible();
});

test('required data and market hint follow the draft timeframe', async ({ page }) => {
	const fiveMinuteDraft = { ...draft, timeframe: '5m' };
	const fiveMinuteEntry = { ...libraryEntry, timeframe: '5m' };
	await page.route(`**/api/v1/strategies/${strategyId}/history`, async (route) => {
		await route.fulfill({
			json: {
				strategy_id: strategyId,
				latest_version: 1,
				next_version: 2,
				versions: [],
				draft: { strategy: fiveMinuteDraft, revision: 1 }
			}
		});
	});
	await page.route(`**/api/v1/strategies/${strategyId}/versions/1`, async (route) => {
		await route.fulfill({ json: { strategy: fiveMinuteDraft, revision: 1 } });
	});
	await page.route(isStrategyLibraryRequest, async (route) => {
		if (route.request().method() !== 'GET') {
			await route.fulfill({ status: 405, json: { detail: 'method not allowed' } });
			return;
		}
		await route.fulfill({ json: { strategies: [fiveMinuteEntry] } });
	});
	await page.goto(`/strategies/${strategyId}`);
	await expect(page.getByRole('heading', { name: 'Builder test trend' })).toBeVisible();
	await expect(
		page.getByText('50 completed 5m bars (OHLCV) before the first signal.')
	).toBeVisible();
	await expect(page.getByText('completed 1h bars')).toHaveCount(0);
	await page.getByRole('button', { name: 'Market and data' }).click();
	await expect(page.getByRole('heading', { name: 'Market and data' })).toBeVisible();
	await expect(page.getByRole('main')).toContainText('any ingested venue clock');
	await expect(page.getByRole('main')).toContainText('this draft uses 5m candles');
	await expect(page.getByRole('main')).toContainText(
		'Sub-hour live requires a connected user-order feed'
	);
});

test('shows a literal editor when the left operand is a literal value', async ({ page }) => {
	await mockDraftStorage(page);
	await page.goto(`/strategies/${strategyId}`);
	await page.getByRole('button', { name: 'Entry conditions' }).click();
	const firstLeft = page.getByLabel('Left operand').first();
	await expect(firstLeft).toHaveValue('indicator:fast');
	await expect(page.getByLabel('Left literal value')).toHaveCount(0);
	await expect(page.getByLabel('Right literal value')).toHaveValue('50');
	await firstLeft.selectOption('literal');
	const leftLiteral = page.getByLabel('Left literal value');
	await expect(leftLiteral).toBeVisible();
	await leftLiteral.fill('42');
	await expect(leftLiteral).toHaveValue('42');
	await firstLeft.selectOption('indicator:fast');
	await expect(page.getByLabel('Left literal value')).toHaveCount(0);
});

test('loads the current draft from identity history without scanning the library', async ({
	page
}) => {
	let libraryRequests = 0;
	await page.route(isStrategyLibraryRequest, async (route) => {
		libraryRequests += 1;
		await route.fulfill({ status: 503, json: { detail: 'Catalog unavailable' } });
	});
	await page.route(`**/api/v1/strategies/${strategyId}/history`, async (route) => {
		await route.fulfill({
			json: {
				strategy_id: strategyId,
				latest_version: 2,
				next_version: 3,
				versions: [],
				draft: { strategy: { ...draft, version: 2 }, revision: 7 }
			}
		});
	});
	await page.goto(`/strategies/${strategyId}`);
	await expect(page.getByRole('heading', { name: 'Builder test trend' })).toBeVisible();
	await expect(page.getByTestId('workspace-version-pill')).toHaveText(/Draft v2/);
	expect(libraryRequests).toBe(0);
});

test('a published-only identity shows its definition read-only in the same layout', async ({
	page
}) => {
	await page.route(isStrategyLibraryRequest, async (route) => {
		await route.fulfill({ status: 503, json: { detail: 'Catalog unavailable' } });
	});
	await page.route(`**/api/v1/strategies/${strategyId}/history`, async (route) => {
		await route.fulfill({
			json: {
				strategy_id: strategyId,
				latest_version: 1,
				next_version: 2,
				versions: [{ version: 1, strategy_fingerprint: fingerprint, published: true }],
				draft: null
			}
		});
	});
	await page.route('**/api/v1/strategies/source/*', (route) =>
		route.fulfill({ json: { strategy: { ...draft, status: 'published' } } })
	);
	await page.goto(`/strategies/${strategyId}`);
	const banner = page.getByRole('status').filter({ hasText: 'No editable draft' });
	await expect(banner).toContainText('immutable');
	await expect(page.getByText('Builder unavailable')).toHaveCount(0);
	await expect(page.getByText('HTTP 404')).toHaveCount(0);
	await expect(page.getByLabel('Strategy name')).toHaveValue('Builder test trend');
	await expect(page.getByLabel('Strategy name')).toBeDisabled();
	await expect(page.getByTestId('plain-english')).toContainText('fast crosses above slow');
	await page.getByRole('button', { name: 'Entry conditions' }).click();
	await expect(page.getByLabel('Left operand').first()).toBeDisabled();
	await expect(page.getByRole('button', { name: 'Save draft' })).toHaveCount(0);
	await expect(page.getByRole('button', { name: /Publish/ })).toHaveCount(0);
	await expect(page.getByTestId('workspace-draft-state')).toContainText('No editable draft');
	await expect(page.getByRole('button', { name: 'Revise into new draft' })).toBeEnabled();
});

test('publishing needs the immutable-version confirmation and never offers paper', async ({
	page
}) => {
	await mockDraftStorage(page);
	let publishBody: { revision: number } | null = null;
	await page.route(`**/api/v1/strategies/${strategyId}/publish`, async (route) => {
		publishBody = (await route.request().postDataJSON()) as { revision: number };
		await route.fulfill({
			json: { strategy_fingerprint: fingerprint, strategy: { ...draft, status: 'published' } }
		});
	});
	await page.goto(`/strategies/${strategyId}`);
	await expect(page.getByText("It doesn't start trading.")).toBeVisible();
	await page.getByRole('button', { name: 'Publish v1…' }).click();
	const dialog = page.getByRole('dialog', { name: 'Publish immutable strategy version?' });
	await expect(dialog).toContainText('Builder test trend · Version 1');
	await expect(dialog).toContainText('BTC / USD · 1h');
	await expect(dialog).toContainText('This version cannot be edited');
	await expect(dialog).toContainText('No blocking definition errors');
	await expect(dialog).toContainText('Created after publication');
	await expect(dialog.getByRole('button', { name: 'Cancel' })).toBeFocused();
	await page.keyboard.press('Escape');
	await expect(dialog).toBeHidden();
	expect(publishBody).toBeNull();
	await page.getByRole('button', { name: 'Publish v1…' }).click();
	await dialog.getByRole('button', { name: 'Publish version' }).click();
	await expect.poll(() => publishBody?.revision).toBe(1);
	const success = page.getByTestId('publish-success');
	await expect(success).toContainText('Published v1');
	await expect(success).toContainText(fingerprint);
	await expect(success.getByRole('link', { name: 'Set up a backtest' })).toHaveAttribute(
		'href',
		`/strategies/${strategyId}/test?version=${encodeURIComponent(fingerprint)}`
	);
	await expect(success.getByRole('link', { name: 'View published version' })).toBeVisible();
	await expect(success.getByRole('link', { name: /paper|deploy|run/i })).toHaveCount(0);
});

test('leaving with unsaved edits asks first and keeps the draft on cancel', async ({ page }) => {
	await mockDraftStorage(page);
	await page.goto(`/strategies/${strategyId}`);
	await page.getByLabel('Strategy name').fill('Edited but unsaved');
	await expect(page.getByTestId('workspace-draft-state')).toContainText('unsaved changes');
	await page
		.getByRole('navigation', { name: 'Strategy stages' })
		.getByRole('link', { name: 'Test' })
		.click();
	const dialog = page.getByRole('dialog', { name: 'Leave with unsaved changes?' });
	await expect(dialog).toBeVisible();
	await dialog.getByRole('button', { name: 'Cancel' }).click();
	await expect(page).toHaveURL(new RegExp(`/strategies/${strategyId}$`));
	await expect(page.getByLabel('Strategy name')).toHaveValue('Edited but unsaved');
	await page
		.getByRole('navigation', { name: 'Strategy stages' })
		.getByRole('link', { name: 'Test' })
		.click();
	await page.getByRole('button', { name: 'Discard changes and leave' }).click();
	await expect(page).toHaveURL(new RegExp(`/strategies/${strategyId}/test$`));
});

test('rule rows read as IF / AND rows and nested groups keep their keyword', async ({ page }) => {
	await mockDraftStorage(page);
	await page.goto(`/strategies/${strategyId}`);
	await page.getByRole('button', { name: 'Entry conditions' }).click();
	const rows = page.locator('.rule-comparison');
	await expect(rows).toHaveCount(2);
	await expect(rows.nth(0).locator('.kw')).toHaveText('IF');
	await expect(rows.nth(1).locator('.kw')).toHaveText('AND');
	await page.getByRole('button', { name: '+ ANY' }).first().click();
	await expect(page.getByText('ANY', { exact: true })).toBeVisible();
	await expect(page.getByTestId('workspace-draft-state')).toContainText('unsaved changes');
});
