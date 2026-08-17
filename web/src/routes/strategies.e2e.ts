import { expect, test } from '@playwright/test';

const strategyId = '01985cf0-7b60-7000-8000-000000000003';

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

test('recovers a durable draft, displays its summary, and saves an edited definition', async ({
	page
}) => {
	await page.route('**/api/v1/strategies?status=draft', async (route) =>
		route.fulfill({
			json: {
				strategies: [
					{
						strategy: draft,
						revision: 1,
						strategy_fingerprint: null,
						archived_at: null,
						summary:
							'BTC-USD · 1h · EMA(20) crosses above EMA(50) · RSI ≥ 50 · 0.5% risk · $10–$100'
					}
				]
			}
		})
	);
	await page.route('**/api/v1/strategies?status=published', async (route) =>
		route.fulfill({ json: { strategies: [] } })
	);
	await page.route('**/api/v1/market-data/datasets', async (route) =>
		route.fulfill({ json: { datasets: [] } })
	);
	await page.route(`**/api/v1/strategies/${strategyId}/versions/1`, async (route) => {
		expect(route.request().method()).toBe('PUT');
		const body = route.request().postDataJSON() as {
			strategy: typeof draft;
			revision: number;
		};
		expect(body.strategy.name).toBe('Saved BTC trend draft');
		expect(body.strategy.sizing.risk_fraction).toBe('0.01');
		expect(body.revision).toBe(1);
		await route.fulfill({
			json: {
				strategy: body.strategy,
				revision: 2,
				summary: 'BTC-USD · 1h · EMA(20) crosses above EMA(50) · RSI ≥ 50 · 1% risk · $10–$100'
			}
		});
	});

	await page.goto('/strategies');
	await expect(page.getByRole('heading', { name: 'Strategy workspace' })).toBeVisible();
	await expect(page.getByRole('textbox', { name: 'Strategy name' })).toHaveValue(
		'Recovered BTC trend draft'
	);
	await expect(page.getByText('EMA(20) crosses above EMA(50)')).toBeVisible();
	await expect(page.getByText('Draft saved')).toBeVisible();
	await page.getByLabel('Strategy name').fill('Saved BTC trend draft');
	await page.getByLabel('Risk per trade').fill('0.01');
	await expect(page.getByText('Draft saved')).not.toBeVisible();
	await expect(page.getByText('0.5% risk')).not.toBeVisible();
	await page.getByRole('button', { name: 'Save draft' }).click();
	await expect(page.getByText('Draft saved')).toBeVisible();
	await expect(page.getByText('1% risk', { exact: false })).toBeVisible();
});

test('serializes draft mutations while a save is in flight', async ({ page }) => {
	let saveRequests = 0;
	let releaseSave: () => void = () => undefined;
	const saveGate = new Promise<void>((resolve) => {
		releaseSave = resolve;
	});
	await page.route('**/api/v1/strategies?status=draft', async (route) =>
		route.fulfill({
			json: {
				strategies: [
					{
						strategy: draft,
						revision: 1,
						strategy_fingerprint: null,
						archived_at: null,
						summary: 'Current durable draft'
					}
				]
			}
		})
	);
	await page.route('**/api/v1/strategies?status=published', async (route) =>
		route.fulfill({ json: { strategies: [] } })
	);
	await page.route('**/api/v1/market-data/datasets', async (route) =>
		route.fulfill({ json: { datasets: [] } })
	);
	await page.route('**/api/v1/strategies/*/versions/1', async (route) => {
		saveRequests += 1;
		const request = route.request().postDataJSON() as { strategy: typeof draft };
		await saveGate;
		await route.fulfill({
			json: { strategy: request.strategy, revision: 2, summary: 'Saved exactly once' }
		});
	});

	await page.goto('/strategies');
	await page.getByLabel('Strategy name').fill('One serialized save');
	const saveButton = page.getByRole('button', { name: 'Save draft' });
	await saveButton.evaluate((button: HTMLButtonElement) => {
		button.click();
		button.click();
	});
	await expect.poll(() => saveRequests).toBe(1);
	await expect(saveButton).toBeDisabled();
	await expect(page.getByRole('button', { name: 'New reference draft' })).toBeDisabled();
	await expect(
		page.getByRole('button', { name: 'Validate & publish immutable version' })
	).toBeDisabled();
	releaseSave();
	await expect(page.getByText('Draft saved')).toBeVisible();
	await expect(page.getByRole('alert')).not.toBeVisible();
	await expect(saveButton).toBeEnabled();
	expect(saveRequests).toBe(1);
});

test('surfaces a stale-save conflict and recovers the current draft after refresh', async ({
	page
}) => {
	await page.route('**/api/v1/strategies?status=draft', async (route) =>
		route.fulfill({
			json: {
				strategies: [
					{
						strategy: draft,
						revision: 2,
						strategy_fingerprint: null,
						archived_at: null,
						summary: 'Current durable server draft'
					}
				]
			}
		})
	);
	await page.route('**/api/v1/strategies?status=published', async (route) =>
		route.fulfill({ json: { strategies: [] } })
	);
	await page.route('**/api/v1/market-data/datasets', async (route) =>
		route.fulfill({ json: { datasets: [] } })
	);
	await page.route(`**/api/v1/strategies/${strategyId}/versions/1`, async (route) => {
		const body = route.request().postDataJSON() as { revision: number };
		expect(body.revision).toBe(2);
		await route.fulfill({
			status: 409,
			json: { detail: 'Strategy draft revision conflict. Refresh and retry your edit.' }
		});
	});

	await page.goto('/strategies');
	await page.getByLabel('Strategy name').fill('Stale browser edit');
	await page.getByRole('button', { name: 'Save draft' }).click();
	await expect(page.getByRole('alert')).toContainText('revision conflict');
	await page.reload();
	await expect(page.getByLabel('Strategy name')).toHaveValue('Recovered BTC trend draft');
});

test('keeps a recovered draft editable when verified datasets are unavailable', async ({
	page
}) => {
	await page.route('**/api/v1/strategies?status=draft', async (route) =>
		route.fulfill({
			json: {
				strategies: [
					{
						strategy: draft,
						revision: 1,
						strategy_fingerprint: null,
						archived_at: null,
						summary: 'Recovered draft remains available'
					}
				]
			}
		})
	);
	await page.route('**/api/v1/strategies?status=published', async (route) =>
		route.fulfill({ json: { strategies: [] } })
	);
	await page.route('**/api/v1/market-data/datasets', async (route) =>
		route.fulfill({ status: 503, json: { detail: 'Verified datasets are unavailable.' } })
	);

	await page.goto('/strategies');
	await expect(page.getByLabel('Strategy name')).toHaveValue('Recovered BTC trend draft');
	await expect(page.getByRole('button', { name: 'Save draft' })).toBeVisible();
	await expect(page.getByRole('alert')).toContainText('Verified datasets are unavailable.');
});

test('suppresses a stale editor when publication reconciliation fails on retry', async ({
	page
}) => {
	let publicationRequests = 0;
	await page.route('**/api/v1/strategies?status=draft', async (route) =>
		route.fulfill({
			json: {
				strategies: [
					{
						strategy: draft,
						revision: 1,
						strategy_fingerprint: null,
						archived_at: null,
						summary: 'Potentially stale draft'
					}
				]
			}
		})
	);
	await page.route('**/api/v1/strategies?status=published', async (route) => {
		publicationRequests += 1;
		if (publicationRequests === 1) {
			await route.fulfill({ json: { strategies: [] } });
			return;
		}
		await route.fulfill({
			status: 503,
			json: { detail: 'Strategy publication catalog is unavailable.' }
		});
	});
	await page.route('**/api/v1/market-data/datasets', async (route) =>
		route.fulfill({ status: 503, json: { detail: 'Verified datasets are unavailable.' } })
	);

	await page.goto('/strategies');
	await expect(page.getByLabel('Strategy name')).toHaveValue('Recovered BTC trend draft');
	await page.getByRole('button', { name: 'Retry workspace load' }).click();
	await expect(page.getByRole('alert')).toContainText(
		'Strategy publication catalog is unavailable.'
	);
	await expect(page.getByLabel('Strategy name')).not.toBeVisible();
});

test('surfaces a failed new-reference-draft mutation without discarding current work', async ({
	page
}) => {
	await page.route('**/api/v1/strategies?status=draft', async (route) =>
		route.fulfill({
			json: {
				strategies: [
					{
						strategy: draft,
						revision: 1,
						strategy_fingerprint: null,
						archived_at: null,
						summary: 'Current durable draft'
					}
				]
			}
		})
	);
	await page.route('**/api/v1/strategies?status=published', async (route) =>
		route.fulfill({ json: { strategies: [] } })
	);
	await page.route('**/api/v1/market-data/datasets', async (route) =>
		route.fulfill({ json: { datasets: [] } })
	);
	await page.route('**/api/v1/strategies', async (route) =>
		route.fulfill({ status: 503, json: { detail: 'Strategy draft storage is unavailable.' } })
	);

	await page.goto('/strategies');
	await expect(page.getByLabel('Strategy name')).toHaveValue('Recovered BTC trend draft');
	await page.getByRole('button', { name: 'New reference draft' }).click();
	await expect(page.getByRole('alert')).toContainText('Strategy draft storage is unavailable.');
	await expect(page.getByLabel('Strategy name')).toHaveValue('Recovered BTC trend draft');
});

test('recovers an active publication after reload without creating a replacement draft', async ({
	page
}) => {
	const recoveredFingerprint = `sha256:${'b'.repeat(64)}`;
	let draftCreated = false;
	await page.route('**/api/v1/strategies?status=draft', async (route) =>
		route.fulfill({ json: { strategies: [] } })
	);
	await page.route('**/api/v1/strategies?status=published', async (route) =>
		route.fulfill({
			json: {
				strategies: [
					{
						strategy: { ...draft, status: 'published' },
						revision: null,
						strategy_fingerprint: recoveredFingerprint,
						archived_at: null,
						summary: 'Recovered immutable publication'
					}
				]
			}
		})
	);
	await page.route('**/api/v1/strategies', async (route) => {
		if (route.request().method() === 'POST') draftCreated = true;
		await route.continue();
	});
	await page.route('**/api/v1/market-data/datasets', async (route) =>
		route.fulfill({ json: { datasets: [] } })
	);

	await page.goto('/strategies');
	await expect(page.getByLabel('Strategy name')).toHaveValue('Recovered BTC trend draft');
	await expect(page.getByText('Recovered immutable publication')).toBeVisible();
	await expect(page.getByRole('button', { name: 'Archive published strategy' })).toBeVisible();
	expect(draftCreated).toBe(false);
});

test('recovers the newest lifecycle artifact instead of an obsolete older draft', async ({
	page
}) => {
	const recoveredFingerprint = `sha256:${'c'.repeat(64)}`;
	await page.route('**/api/v1/strategies?status=draft', async (route) =>
		route.fulfill({
			json: {
				strategies: [
					{
						strategy: {
							...draft,
							name: 'Obsolete older draft',
							created_at: '2026-08-13T12:00:00Z'
						},
						revision: 1,
						strategy_fingerprint: null,
						archived_at: null,
						summary: 'Obsolete draft summary'
					}
				]
			}
		})
	);
	await page.route('**/api/v1/strategies?status=published', async (route) =>
		route.fulfill({
			json: {
				strategies: [
					{
						strategy: {
							...draft,
							name: 'Newest immutable publication',
							status: 'published',
							created_at: '2026-08-14T12:00:00Z'
						},
						revision: null,
						strategy_fingerprint: recoveredFingerprint,
						archived_at: null,
						summary: 'Newest publication summary'
					}
				]
			}
		})
	);
	await page.route('**/api/v1/market-data/datasets', async (route) =>
		route.fulfill({ json: { datasets: [] } })
	);

	await page.goto('/strategies');
	await expect(page.getByLabel('Strategy name')).toHaveValue('Newest immutable publication');
	await expect(page.getByText('Newest publication summary')).toBeVisible();
	await expect(page.getByRole('button', { name: 'Archive published strategy' })).toBeVisible();
});

test('archives a published strategy from the browser without changing its evidence', async ({
	page
}) => {
	const fingerprint = `sha256:${'a'.repeat(64)}`;
	await page.route('**/api/v1/strategies?status=draft', async (route) =>
		route.fulfill({
			json: {
				strategies: [
					{
						strategy: draft,
						revision: 1,
						strategy_fingerprint: null,
						archived_at: null,
						summary: 'Recovered draft'
					}
				]
			}
		})
	);
	await page.route('**/api/v1/strategies?status=published', async (route) =>
		route.fulfill({ json: { strategies: [] } })
	);
	await page.route('**/api/v1/market-data/datasets', async (route) =>
		route.fulfill({ json: { datasets: [] } })
	);
	await page.route(`**/api/v1/strategies/${strategyId}/publish`, async (route) => {
		const body = route.request().postDataJSON() as { revision: number };
		expect(body.revision).toBe(1);
		await route.fulfill({
			json: { strategy_fingerprint: fingerprint, strategy: { ...draft, status: 'published' } }
		});
	});
	await page.route('**/api/v1/strategies/**/archive', async (route) => {
		expect(route.request().method()).toBe('POST');
		await route.fulfill({
			json: { strategy_fingerprint: fingerprint, archived_at: '2026-08-14T12:30:00Z' }
		});
	});

	await page.goto('/strategies');
	await page.getByRole('button', { name: 'Validate & publish immutable version' }).click();
	await expect(page.getByRole('button', { name: 'Archive published strategy' })).toBeVisible();
	await expect(page.getByText('Draft saved')).not.toBeVisible();
	await page.getByRole('button', { name: 'Archive published strategy' }).click();
	await expect(page.getByText('Published strategy archived')).toBeVisible();
});
