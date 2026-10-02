import { expect, isStrategyLibraryRequest, test } from '../e2e/harness';
import { RETIRED_ENGINE_PREFIX } from '../e2e/workspace-fixtures';

const strategyId = '01985cf0-7b60-7000-8000-000000000007';
const fingerprint = `sha256:${'c'.repeat(64)}`;

const draft = {
	schema_version: '1.0',
	strategy_id: strategyId,
	name: 'Builder test trend',
	description: 'Reference research strategy; not trading authority.',
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

function record(strategy: typeof draft = draft, revision = 1, overrides: object = {}) {
	return {
		strategy_id: strategyId,
		name: strategy.name,
		revision,
		created_at: strategy.created_at,
		updated_at: strategy.created_at,
		document: strategy,
		strategy,
		validation: { valid: true, issues: [] },
		current_fingerprint: fingerprint,
		summary: null,
		product_id: strategy.instrument.product_id,
		timeframe: strategy.timeframe,
		...overrides
	};
}

async function mockDraftStorage(
	page: import('@playwright/test').Page,
	current: ReturnType<typeof record> = record()
): Promise<void> {
	await page.route(`**/api/v1/strategies/${strategyId}`, async (route) => {
		await route.fulfill({ json: current });
	});
	await page.route(isStrategyLibraryRequest, async (route) => {
		await route.fulfill({ status: 503, json: { detail: 'Library must not be needed here' } });
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
	await expect(page.getByRole('button', { name: 'Save', exact: true })).toBeEnabled();
	await expect(page.getByRole('button', { name: /Publish/ })).toHaveCount(0);
	await expect(page.getByTestId('saved-validation')).toContainText('Saved definition is valid');
});

test('marks unsaved changes and still allows saving work in progress with problems', async ({
	page
}) => {
	await mockDraftStorage(page);
	await page.goto(`/strategies/${strategyId}`);
	await page.getByLabel('Strategy name').fill('');
	await expect(page.locator('.dirty-pill')).toBeVisible();
	await expect(page.locator('.problems')).toContainText('Name is required.');
	await expect(page.getByRole('button', { name: 'Save', exact: true })).toBeEnabled();
	await expect(page.getByText('You can save work in progress with problems')).toBeVisible();
});

test('saving an invalid definition keeps it and shows the saved validation result', async ({
	page
}) => {
	let saved = false;
	await page.route(`**/api/v1/strategies/${strategyId}`, async (route) => {
		if (route.request().method() === 'PUT') {
			const body = (await route.request().postDataJSON()) as { document: typeof draft };
			saved = true;
			await route.fulfill({
				json: record(body.document, 2, {
					strategy: null,
					current_fingerprint: null,
					validation: {
						valid: false,
						issues: [{ loc: 'name', message: 'String should have at least 1 character' }]
					}
				})
			});
			return;
		}
		await route.fulfill({ json: record() });
	});
	await page.goto(`/strategies/${strategyId}`);
	await page.getByLabel('Strategy name').fill('');
	await page.getByRole('button', { name: 'Save', exact: true }).click();
	await expect.poll(() => saved).toBe(true);
	const validation = page.getByTestId('saved-validation');
	await expect(validation).toContainText('Saved definition has 1 problem');
	await expect(validation).toContainText('String should have at least 1 character');
	await expect(page.getByTestId('workspace-validity')).toHaveText(/1 problem/);
	await expect(page.getByTestId('workspace-save-state')).toContainText('Revision 2');
});

test('a stale save is rejected and never overwrites', async ({ page }) => {
	let puts = 0;
	let reloaded = false;
	await page.route(`**/api/v1/strategies/${strategyId}`, async (route) => {
		if (route.request().method() === 'PUT') {
			puts += 1;
			await route.fulfill({
				status: 409,
				json: {
					detail: {
						code: 'strategy_revision_conflict',
						message: 'Strategy revision conflict.',
						current_revision: 5
					}
				}
			});
			return;
		}
		if (puts > 0) reloaded = true;
		await route.fulfill({
			json: puts > 0 ? record({ ...draft, name: 'Saved elsewhere' }, 5) : record()
		});
	});
	await page.goto(`/strategies/${strategyId}`);
	await page.getByLabel('Strategy name').fill('My local edit');
	await page.getByRole('button', { name: 'Save', exact: true }).click();
	const conflict = page.getByTestId('save-conflict');
	await expect(conflict).toContainText('Not saved: this strategy changed elsewhere');
	await expect(conflict).toContainText('revision 5');
	await expect(conflict).toContainText('Nothing was overwritten');
	await expect(page.getByLabel('Strategy name')).toHaveValue('My local edit');
	await expect(page.getByRole('button', { name: 'Save', exact: true })).toBeDisabled();
	expect(puts).toBe(1);
	await conflict.getByRole('button', { name: 'Reload latest' }).click();
	await expect.poll(() => reloaded).toBe(true);
	await expect(page.getByLabel('Strategy name')).toHaveValue('Saved elsewhere');
	await expect(page.getByTestId('save-conflict')).toHaveCount(0);
});

test('a saved document the form cannot show opens as JSON with its problems', async ({ page }) => {
	const wip = { schema_version: '1.0', strategy_id: strategyId, name: 'Half-built idea' };
	let savedDocument: unknown = null;
	await page.route(`**/api/v1/strategies/${strategyId}`, async (route) => {
		if (route.request().method() === 'PUT') {
			savedDocument = ((await route.request().postDataJSON()) as { document: unknown }).document;
			await route.fulfill({ json: record(draft, 3) });
			return;
		}
		await route.fulfill({
			json: {
				...record(),
				name: 'Half-built idea',
				document: wip,
				strategy: null,
				current_fingerprint: null,
				product_id: null,
				timeframe: null,
				validation: { valid: false, issues: [{ loc: 'instrument', message: 'Field required' }] }
			}
		});
	});
	await page.goto(`/strategies/${strategyId}`);
	const editor = page.getByTestId('raw-definition');
	await expect(editor).toBeVisible();
	await expect(page.getByTestId('saved-validation')).toContainText('Field required');
	await editor.fill(JSON.stringify(draft));
	await page.getByRole('button', { name: 'Save', exact: true }).click();
	await expect
		.poll(() => (savedDocument as { name?: string } | null)?.name)
		.toBe('Builder test trend');
});

test('the Build inspector explains how backtests simulate without engine variants', async ({
	page
}) => {
	await mockDraftStorage(page);
	await page.goto(`/strategies/${strategyId}`);
	const inspector = page.getByRole('complementary', { name: 'Strategy inspector' });
	await expect(inspector).toBeVisible();
	await expect(page.getByRole('table', { name: 'Engine support matrix' })).toHaveCount(0);
	const disclosure = inspector.getByTestId('backtest-model-disclosure');
	await disclosure.getByText('How backtests simulate').click();
	await expect(disclosure).toContainText('Signals on completed candles');
	await expect(disclosure).toContainText('Maker-limit entries rest');
	await expect(disclosure).toContainText('Stops and targets on bar extremes');
	await expect(disclosure).toContainText('Optional spread stress');
	await expect(disclosure).toContainText("Candles don't show queue position");
	await expect(disclosure).toContainText('max_entry_wait_bars');
	await expect(disclosure).toContainText('not a promise');
	expect(await disclosure.getByTestId('backtest-model-assumption').count()).toBeGreaterThan(8);
	const text = await inspector.innerText();
	expect(text).not.toMatch(/\bV[1-4]\b/);
	expect(text).not.toContain(RETIRED_ENGINE_PREFIX);
});

test('saves the document in place with the loaded revision', async ({ page }) => {
	type SavedPayload = {
		document: { name: string; version?: number; status?: string };
		revision: number;
	};
	let savedBody = null as SavedPayload | null;
	await page.route(`**/api/v1/strategies/${strategyId}`, async (route) => {
		if (route.request().method() === 'PUT') {
			savedBody = (await route.request().postDataJSON()) as SavedPayload;
			await route.fulfill({
				json: record({ ...draft, name: savedBody.document.name }, 2)
			});
			return;
		}
		await route.fulfill({ json: record() });
	});
	await page.goto(`/strategies/${strategyId}`);
	await page.getByLabel('Strategy name').fill('Renamed in builder');
	await page.getByRole('button', { name: 'Save', exact: true }).click();
	await expect.poll(() => savedBody?.revision).toBe(1);
	const saved = savedBody as SavedPayload;
	expect(saved.document.name).toBe('Renamed in builder');
	expect(saved.document.version).toBeUndefined();
	expect(saved.document.status).toBeUndefined();
	await expect(page.getByText(/^Saved \d/)).toBeVisible();
	await expect(page.getByTestId('workspace-save-state')).toContainText('Revision 2');
	await expect(page.getByTestId('workspace-save-state')).toContainText('all edits saved');
});

test('market product and timeframe are editable and saved with a matching quote', async ({
	page
}) => {
	type SavedInstrument = {
		document: {
			instrument: { product_id: string; base_currency: string; quote_currency: string };
			timeframe: string;
		};
	};
	let savedBody = null as SavedInstrument | null;
	await page.route(`**/api/v1/strategies/${strategyId}`, async (route) => {
		if (route.request().method() === 'PUT') {
			savedBody = (await route.request().postDataJSON()) as SavedInstrument;
			await route.fulfill({ json: record(draft, 2) });
			return;
		}
		await route.fulfill({ json: record() });
	});
	await page.goto(`/strategies/${strategyId}`);
	await page.getByRole('button', { name: 'Market and data' }).click();
	await page.getByLabel('Product', { exact: true }).fill('eth-usdc');
	await page.getByLabel('Timeframe', { exact: true }).selectOption('4h');
	await page.getByRole('button', { name: 'Save', exact: true }).click();
	await expect.poll(() => savedBody?.document.instrument.product_id).toBe('ETH-USDC');
	const saved = savedBody as SavedInstrument;
	expect(saved.document.instrument.base_currency).toBe('ETH');
	expect(saved.document.instrument.quote_currency).toBe('USDC');
	expect(saved.document.timeframe).toBe('4h');
});

test('required data and market hint follow the draft timeframe', async ({ page }) => {
	const fiveMinuteDraft = { ...draft, timeframe: '5m' };
	await mockDraftStorage(page, record(fiveMinuteDraft));
	await page.goto(`/strategies/${strategyId}`);
	await expect(page.getByRole('heading', { name: 'Builder test trend' })).toBeVisible();
	await expect(
		page.getByText('50 completed 5m bars (OHLCV) before the first signal.')
	).toBeVisible();
	await expect(page.getByText('completed 1h bars')).toHaveCount(0);
	await page.getByRole('button', { name: 'Market and data' }).click();
	await expect(page.getByRole('heading', { name: 'Market and data' })).toBeVisible();
	await expect(page.getByRole('main')).toContainText('any ingested venue clock');
	await expect(page.getByRole('main')).toContainText('this strategy uses 5m candles');
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

test('loads the strategy record without scanning the library', async ({ page }) => {
	let libraryRequests = 0;
	await page.route(isStrategyLibraryRequest, async (route) => {
		libraryRequests += 1;
		await route.fulfill({ status: 503, json: { detail: 'Catalog unavailable' } });
	});
	await page.route(`**/api/v1/strategies/${strategyId}`, async (route) => {
		await route.fulfill({ json: record(draft, 7) });
	});
	await page.goto(`/strategies/${strategyId}`);
	await expect(page.getByRole('heading', { name: 'Builder test trend' })).toBeVisible();
	await expect(page.getByTestId('workspace-save-state')).toContainText('Revision 7');
	expect(libraryRequests).toBe(0);
});

test('leaving with unsaved edits asks first and keeps the draft on cancel', async ({ page }) => {
	await mockDraftStorage(page);
	await page.goto(`/strategies/${strategyId}`);
	await page.getByLabel('Strategy name').fill('Edited but unsaved');
	await expect(page.getByTestId('workspace-save-state')).toContainText('unsaved changes');
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
	await expect(page.getByTestId('workspace-save-state')).toContainText('unsaved changes');
});

test('entry preference offers only maker-only entries, even for a legacy draft', async ({
	page
}) => {
	const legacyDraft = {
		...draft,
		execution: { ...draft.execution, entry_preference: 'marketable_limit' }
	};
	const issue = { loc: 'execution.entry_preference', message: 'Input should be maker_only' };
	await mockDraftStorage(
		page,
		record(legacyDraft, 1, { validation: { valid: false, issues: [issue] } })
	);
	await page.goto(`/strategies/${strategyId}`);
	await page.getByRole('button', { name: 'Execution preferences' }).click();
	const select = page.getByLabel('Entry preference');
	await expect(select.locator('option')).toHaveText(['Maker only']);
	await expect(select.locator('option[value="marketable_limit"]')).toHaveCount(0);
	await expect(page.getByText('Entries are always post-only maker limit orders.')).toBeVisible();
	// The backend validation issue explains the retired value; the form never offers it.
	await expect(page.getByTestId('saved-validation')).toContainText('Input should be maker_only');
});

test('the kind picker searches the catalog and shows the chosen kind’s parameters', async ({
	page
}) => {
	type SavedIndicators = {
		document: {
			indicators: Record<string, unknown>[];
			entry: { when: { all: { left: Record<string, unknown> }[] } };
		};
	};
	let savedBody = null as SavedIndicators | null;
	await page.route(`**/api/v1/strategies/${strategyId}`, async (route) => {
		if (route.request().method() === 'PUT') {
			savedBody = (await route.request().postDataJSON()) as SavedIndicators;
			await route.fulfill({ json: record(draft, 2) });
			return;
		}
		await route.fulfill({ json: record() });
	});
	await page.goto(`/strategies/${strategyId}`);
	await page.getByRole('button', { name: 'Indicators', exact: true }).click();
	const row = page.getByTestId('indicator-row').first();
	const kindButton = row.getByRole('button', { name: /^Kind EMA/ });
	await expect(kindButton).toHaveAttribute('aria-expanded', 'false');
	await kindButton.click();
	const search = page.getByRole('combobox', { name: 'Search indicators' });
	await expect(search).toBeFocused();
	const listbox = page.getByRole('listbox', { name: 'Indicator kinds' });
	await expect(listbox.getByRole('group', { name: 'Momentum' })).toBeVisible();
	await search.fill('super');
	await expect(listbox.getByRole('option')).toHaveCount(1);
	await expect(listbox.getByRole('option', { name: /Supertrend/ })).toBeVisible();
	await search.press('Enter');
	await expect(listbox).toHaveCount(0);
	await expect(row.getByRole('button', { name: /^Kind Supertrend/ })).toBeFocused();
	// EMA(20) -> Supertrend keeps the same-named, still-valid period; multiplier defaults.
	await expect(row.getByLabel('ATR period')).toHaveValue('20');
	await expect(row.getByLabel('Multiplier')).toHaveValue('3');
	await expect(row.getByText('Wilder ATR period.')).toBeVisible();
	await expect(row.getByText(/ATR band that trails the trend/)).toBeVisible();
	await row.getByLabel('Offset (bars ago)').fill('1');
	await expect(row.getByText('Needs 21 completed bars.')).toBeVisible();

	await page.getByRole('button', { name: 'Entry conditions' }).click();
	const firstLeft = page.getByLabel('Left operand').first();
	await expect(
		firstLeft.locator('optgroup[label="Supertrend(20, 3) · 1 bar ago — fast"] option')
	).toHaveText([
		'Supertrend(20, 3) · value · 1 bar ago',
		'Supertrend(20, 3) · direction · 1 bar ago'
	]);
	await firstLeft.selectOption('indicator:fast.direction');
	await expect(firstLeft).toHaveValue('indicator:fast.direction');

	await page.getByRole('button', { name: 'Save', exact: true }).click();
	await expect.poll(() => savedBody?.document.indicators[0]?.kind).toBe('supertrend');
	const saved = savedBody as SavedIndicators;
	expect(saved.document.indicators[0]).toEqual({
		id: 'fast',
		kind: 'supertrend',
		input: ['high', 'low', 'close'],
		parameters: { period: 20, multiplier: '3' },
		offset: 1
	});
	expect(saved.document.entry.when.all[0]?.left).toEqual({
		indicator: 'fast',
		series: 'direction'
	});
});

test('the kind picker closes on Escape and returns focus without changing the kind', async ({
	page
}) => {
	await mockDraftStorage(page);
	await page.goto(`/strategies/${strategyId}`);
	await page.getByRole('button', { name: 'Indicators', exact: true }).click();
	const row = page.getByTestId('indicator-row').nth(2);
	const kindButton = row.getByRole('button', { name: /^Kind RSI/ });
	await kindButton.click();
	const search = page.getByRole('combobox', { name: 'Search indicators' });
	await search.press('ArrowDown');
	await search.press('Escape');
	await expect(page.getByRole('listbox', { name: 'Indicator kinds' })).toHaveCount(0);
	await expect(kindButton).toBeFocused();
	await expect(row.getByLabel('Period')).toHaveValue('14');
	await expect(page.getByTestId('workspace-save-state')).not.toContainText('unsaved changes');
});

test('position sizing labels and the summary follow the product quote currency', async ({
	page
}) => {
	await mockDraftStorage(page);
	await page.goto(`/strategies/${strategyId}`);
	await page.getByRole('button', { name: 'Position sizing' }).click();
	await expect(page.getByLabel('Minimum USD notional')).toHaveValue('10');
	await expect(page.getByLabel('Maximum USD notional')).toHaveValue('100');
	await page.getByRole('button', { name: 'Market and data' }).click();
	await page.getByLabel('Product', { exact: true }).fill('eth-usdc');
	await page.getByRole('button', { name: 'Position sizing' }).click();
	await expect(page.getByLabel('Minimum USDC notional')).toHaveValue('10');
	await expect(page.getByLabel('Maximum USDC notional')).toHaveValue('100');
	await expect(page.getByText(/USD notional/)).toHaveCount(0);
	await expect(page.locator('.inspector-block').first()).toContainText(
		'between 10 USDC and 100 USDC'
	);
});

test('take profit can be none and saves the canonical {kind: none} (ADR 0090)', async ({
	page
}) => {
	let saved = null as { document: { exits: { take_profit: unknown } } } | null;
	await page.route(`**/api/v1/strategies/${strategyId}`, async (route) => {
		if (route.request().method() === 'PUT') {
			saved = (await route.request().postDataJSON()) as typeof saved;
			await route.fulfill({ json: record(draft, 2) });
			return;
		}
		await route.fulfill({ json: record() });
	});
	await page.goto(`/strategies/${strategyId}`);
	await page.getByRole('button', { name: 'Exit conditions and protective stops' }).click();
	await expect(page.getByLabel('Take profit — reward/risk multiple')).toHaveValue('2');
	await page.getByLabel('Take profit kind').selectOption('none');
	await expect(page.getByLabel('Take profit — reward/risk multiple')).toHaveCount(0);
	await expect(page.getByText('No take-profit order rests.')).toBeVisible();
	await page.getByRole('button', { name: 'Save', exact: true }).click();
	await expect.poll(() => saved?.document.exits.take_profit).toEqual({ kind: 'none' });
	await page.getByLabel('Take profit kind').selectOption('reward_risk');
	await expect(page.getByLabel('Take profit — reward/risk multiple')).toHaveValue('2');
});
