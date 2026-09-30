/**
 * Test stage of the strategy workspace (absorbs /research and, per strategy,
 * /backtests) plus the /research redirect and chooser.
 */
import { expect, test } from '../e2e/harness';
import {
	backtestEntry,
	datasetFingerprint,
	definition,
	fingerprint,
	fingerprintV2,
	invalidRecord,
	mockBacktestDetail,
	mockBacktestList,
	mockDatasets,
	mockFees,
	mockStrategy,
	REMOVED_ENGINE_FIELD,
	RETIRED_ENGINE_PREFIX,
	RETIRED_ENGINE_ROUTE,
	resultFingerprint,
	strategyId,
	strategyRecord,
	suggestedFeeProfile
} from '../e2e/workspace-fixtures';

const testStage = `/strategies/${strategyId}/test`;

test('old /research links redirect to the Test stage of the owning strategy', async ({ page }) => {
	await mockStrategy(page);
	await mockBacktestList(page);
	await mockDatasets(page);
	await page.goto(`/research?strategy=${strategyId}&strategy_fingerprint=${fingerprint}`);
	await expect(page).toHaveURL(new RegExp(`/strategies/${strategyId}/test$`));
	await expect(page.getByTestId('workspace-name')).toHaveText('Recovered BTC trend draft');
	await page.goto(`/research?strategy_fingerprint=${encodeURIComponent(fingerprintV2)}`);
	await expect(page).toHaveURL(new RegExp(`/strategies/${strategyId}/test$`));
});

test('/research without a strategy is a chooser that points at the library', async ({ page }) => {
	await page.goto('/research');
	await expect(page.getByTestId('stage-chooser')).toContainText('Test stage');
	await expect(page.getByRole('link', { name: 'Open a strategy' })).toHaveAttribute(
		'href',
		'/strategies'
	);
	const nav = page.getByRole('navigation', { name: 'Primary navigation' });
	await expect(nav.getByRole('link', { name: 'Strategies' })).toHaveAttribute(
		'aria-current',
		'page'
	);
	await expect(page.getByTestId('breadcrumb')).toHaveText(/Strategies\s*\/\s*Research/);
});

test('runs a backtest of the current rules and marks earlier-edit results', async ({ page }) => {
	await mockStrategy(page);
	await mockFees(page, suggestedFeeProfile());
	await mockDatasets(page);
	const v2Result = `sha256:${'9'.repeat(64)}`;
	const listed: string[] = [];
	await mockBacktestList(page, (requested) => {
		listed.push(requested);
		return [
			backtestEntry(fingerprintV2, {
				result_fingerprint: v2Result
			}),
			backtestEntry(fingerprint, {
				published_at: '2026-09-22T10:00:00Z',
				summary: { ...backtestEntry(fingerprint).summary, total_return_fraction: '0.11' }
			})
		];
	});
	await mockBacktestDetail(page, v2Result, fingerprintV2);
	let launchBody: Record<string, unknown> | null = null;
	await page.route(
		(url) => url.pathname === '/api/v1/backtests',
		async (route) => {
			if (route.request().method() !== 'POST') return route.fallback();
			launchBody = (await route.request().postDataJSON()) as Record<string, unknown>;
			await route.fulfill({
				status: 201,
				json: {
					run_fingerprint: `sha256:${'e'.repeat(64)}`,
					result_fingerprint: v2Result,
					strategy_id: strategyId,
					strategy_fingerprint: fingerprint
				}
			});
		}
	);

	await page.goto(testStage);
	// Assumptions stay visible in the run bar, and the framing is explicit.
	await expect(page.getByText(/research\s+evidence, not a promise/)).toBeVisible();
	await expect(page.getByLabel('Verified 1h dataset')).toHaveValue(datasetFingerprint);
	await expect(page.getByLabel('Maker fee rate')).toHaveValue('0.0025');
	await expect(page.getByLabel('Taker fee rate')).toHaveValue('0.0040');
	await expect(page.getByLabel('Initial capital (USDC)')).toHaveValue('10000');
	// One backtest model: no engine picker anywhere in the run bar or study builder.
	await expect(page.getByLabel('Engine')).toHaveCount(0);
	// Custom dates, slippage, and spread stress live behind the Advanced disclosure.
	await expect(page.getByText(/\(UTC, 1h bars\)/)).toBeHidden();
	await page.getByTestId('run-advanced').locator('summary').click();
	await expect(page.getByText(/\(UTC, 1h bars\)/)).toBeVisible();

	// Results list covers every run of this strategy, by strategy id.
	expect(listed[0]).toBe(strategyId);
	const results = page.getByRole('table', { name: 'Backtest results for this strategy' });
	await expect(results.getByRole('row')).toHaveCount(3);
	const earlier = results.locator('[data-rules="earlier"]');
	await expect(earlier).toContainText('Earlier edit');
	await expect(results.locator('[data-rules="current"]')).toContainText('Current rules');
	// "What changed" diffs the earlier snapshot against the current rules.
	await earlier.getByRole('button', { name: 'What changed' }).click();
	const diff = results.getByTestId('snapshot-diff');
	await expect(diff.getByRole('table', { name: 'What changed since these rules' })).toBeVisible();
	await expect(diff).toContainText('12');
	await expect(diff).toContainText('21');
	await page.getByLabel('Only current rules').check();
	await expect(results.getByRole('row')).toHaveCount(2);
	await expect(results.locator('[data-rules="earlier"]')).toHaveCount(0);
	await page.getByLabel('Only current rules').uncheck();

	await page.getByLabel('Spread stress (bps, total bid-ask, optional)').fill('8');
	await expect(page.getByTestId('run-advanced').locator('summary')).toContainText(
		'spread stress 8 bps'
	);
	await page.getByRole('button', { name: 'Run backtest' }).click();
	await expect.poll(() => launchBody).not.toBeNull();
	expect(launchBody).not.toHaveProperty('strategy_fingerprint');
	expect(launchBody).not.toHaveProperty(REMOVED_ENGINE_FIELD);
	expect(launchBody).toMatchObject({
		strategy_id: strategyId,
		spread_bps: '8',
		maker_fee_rate: '0.0025',
		taker_fee_rate: '0.0040'
	});

	// The new result opens inline, deep-linked by ?result=.
	await expect.poll(() => new URL(page.url()).searchParams.get('result')).toBe(v2Result);
	const result = page.getByTestId('workspace-result');
	await expect(result.getByText('Simulated result (candle-based fills)')).toBeVisible();
	await expect(result.getByTestId('modeled-assumptions')).toBeVisible();
	await expect(result.getByText(/research\s+evidence, not a promise/)).toBeVisible();
	// Compact result header: rules · period, plus the simulated-result chip.
	const head = result.getByTestId('backtest-result-head');
	await expect(head).toContainText(
		/Earlier edit · \d{4}-\d{2}-\d{2} → \d{4}-\d{2}-\d{2} · \d+ bars/
	);
	await expect(head.getByText('Simulated result (candle-based fills)')).toBeVisible();
	const metrics = result.getByTestId('result-metrics');
	for (const label of [
		'Net return',
		'Buy & hold',
		'Max drawdown',
		'Trades',
		'Win rate',
		'Profit factor'
	])
		await expect(metrics).toContainText(label);
	// Fingerprints sit in the collapsed Evidence row.
	const evidence = result.getByTestId('result-evidence');
	await expect(evidence).not.toHaveAttribute('open', '');
	await evidence.locator('summary').click();
	await expect(evidence).toContainText('Dataset');
	await expect(result.getByTestId('backtest-equity-chart')).toBeVisible();
	// No promotion path from a backtest result to a runtime.
	await expect(page.getByRole('button', { name: /Deploy|Start paper/ })).toHaveCount(0);
	await expect(page.getByRole('link', { name: /Deploy|Start paper/ })).toHaveCount(0);

	await result.getByRole('button', { name: '× Close result' }).click();
	await expect(result).toHaveCount(0);
	await expect.poll(() => new URL(page.url()).searchParams.get('result')).toBeNull();
});

test('a ?result= deep link opens inline, and a foreign result is refused', async ({ page }) => {
	await mockStrategy(page);
	await mockBacktestList(page, () => [backtestEntry(fingerprint)]);
	await mockDatasets(page);
	await mockBacktestDetail(page, resultFingerprint, fingerprint);
	await page.goto(`${testStage}?result=${resultFingerprint}`);
	await expect(
		page.getByTestId('workspace-result').getByText('Simulated result (candle-based fills)')
	).toBeVisible();
	await expect(page.getByRole('link', { name: /Inspect result/ })).toHaveAttribute(
		'aria-current',
		'true'
	);

	const foreign = `sha256:${'7'.repeat(64)}`;
	await mockBacktestDetail(page, foreign, `sha256:${'6'.repeat(64)}`);
	await page.goto(`${testStage}?result=${foreign}`);
	await expect(page.getByTestId('workspace-result')).toContainText(
		'This result does not belong to this strategy.'
	);
	await expect(page.getByText('Simulated result (candle-based fills)')).toHaveCount(0);
});

test('a composed study runs from the Run a study disclosure against the current rules', async ({
	page
}) => {
	await mockStrategy(page);
	await mockFees(page, suggestedFeeProfile());
	await mockDatasets(page);
	await mockBacktestList(page);
	let studyBody: Record<string, unknown> | null = null;
	await page.route('**/api/v1/research/studies', async (route) => {
		studyBody = (await route.request().postDataJSON()) as Record<string, unknown>;
		await route.fulfill({ status: 422, json: { detail: 'study rejected in test' } });
	});
	await page.goto(testStage);
	await expect(page.getByLabel('Study')).toBeHidden();
	await page.getByText('Run a study').click();
	await expect(page.getByRole('option', { name: 'OOS holdout' })).toBeAttached();
	await expect(page.getByRole('option', { name: 'Walk-forward', exact: true })).toBeAttached();
	await expect(page.getByRole('option', { name: 'Parameter sweep' })).toBeAttached();
	await expect(page.getByRole('option', { name: 'Walk-forward optimization' })).toBeAttached();
	await expect(page.getByRole('option', { name: 'Single window' })).toHaveCount(0);
	await expect(page.getByLabel('Engine')).toHaveCount(0);
	await expect(page.getByRole('combobox', { name: /engine/i })).toHaveCount(0);
	await page.getByLabel('Study').selectOption('oos_holdout');
	await page.getByRole('button', { name: 'Run study' }).click();
	await expect.poll(() => studyBody).not.toBeNull();
	expect(studyBody).toMatchObject({ kind: 'oos_holdout', strategy_id: strategyId });
	expect(studyBody).not.toHaveProperty('strategy_fingerprint');
	expect(studyBody).not.toHaveProperty(REMOVED_ENGINE_FIELD);
	// Blank spread stress is omitted, not sent as zero or null.
	expect(studyBody).not.toHaveProperty('spread_bps');
	await expect(page.getByRole('alert')).toContainText('study rejected in test');
});

test('loads every result page for the strategy', async ({ page }) => {
	await mockStrategy(page);
	await mockDatasets(page);
	const offsets: number[] = [];
	await page.route(
		(url) => url.pathname === '/api/v1/backtests' && url.searchParams.has('strategy_id'),
		(route) => {
			const url = new URL(route.request().url());
			const offset = Number(url.searchParams.get('offset') ?? '0');
			const limit = Number(url.searchParams.get('limit') ?? '50');
			offsets.push(offset);
			const count = offset === 0 ? limit : 1;
			return route.fulfill({
				json: {
					entries: Array.from({ length: count }, (_, index) =>
						backtestEntry(fingerprint, {
							result_fingerprint: `sha256:${(offset + index + 1).toString(16).padStart(64, '0')}`,
							summary: {
								...backtestEntry(fingerprint).summary,
								total_return_fraction: offset > 0 ? '0.21' : '0.01'
							}
						})
					),
					limit,
					offset,
					returned: count
				}
			});
		}
	);
	await page.goto(testStage);
	await expect(page.getByRole('cell', { name: '21.00%', exact: true })).toBeVisible();
	expect(offsets).toEqual([0, 50]);
});

test('a failed dataset catalog keeps the stage usable but blocks the run', async ({ page }) => {
	await mockStrategy(page);
	await mockBacktestList(page);
	await page.route('**/api/v1/market-data/datasets/latest', (route) =>
		route.fulfill({ status: 503, json: { detail: 'Verified datasets are unavailable.' } })
	);
	await page.goto(testStage);
	await expect(page.getByRole('alert')).toContainText('Verified datasets are unavailable.');
	await expect(page.getByRole('button', { name: 'Run backtest' })).toBeDisabled();
});

test('maker/taker prefill from the fee tier and keep a custom override', async ({ page }) => {
	await mockStrategy(page);
	await mockFees(page, suggestedFeeProfile());
	await mockDatasets(page);
	await mockBacktestList(page);
	await page.goto(testStage);
	await expect(page.getByTestId('research-fee-source')).toContainText(
		'Suggested from Coinbase fee tier'
	);
	await page.getByLabel('Maker fee rate').fill('0.001');
	await expect(page.getByTestId('research-fee-source')).toHaveText('Custom');
	await page.getByRole('button', { name: 'Apply suggested rates' }).click();
	await expect(page.getByLabel('Maker fee rate')).toHaveValue('0.0025');
});

test('fees stay blank when the Coinbase fee tier cannot be suggested', async ({ page }) => {
	await mockStrategy(page);
	await mockFees(page, {}, 502);
	await mockDatasets(page);
	await mockBacktestList(page);
	await page.goto(testStage);
	await expect(page.getByTestId('research-fee-source')).toHaveText(
		'Coinbase fee-tier suggestion unavailable. Enter modeled rates.'
	);
	await expect(page.getByLabel('Maker fee rate')).toHaveValue('');
	await expect(page.getByLabel('Taker fee rate')).toHaveValue('');
});

test('a refreshed fee suggestion never overwrites in-progress fee edits', async ({ page }) => {
	await mockStrategy(page);
	await mockDatasets(page);
	await mockBacktestList(page);
	let generation = 0;
	await page.route('**/api/v1/fees', (route) => {
		const fetchedAt = generation === 0 ? '2026-09-13T16:00:00Z' : '2026-09-13T18:00:00Z';
		return route.fulfill({
			json: suggestedFeeProfile({
				suggested_maker_fee_rate: generation === 0 ? '0.0025' : '0.0015',
				suggested_taker_fee_rate: generation === 0 ? '0.0040' : '0.0025',
				suggestion_fetched_at: fetchedAt,
				as_of: fetchedAt
			})
		});
	});
	await page.goto(testStage);
	await expect(page.getByLabel('Maker fee rate')).toHaveValue('0.0025');
	generation = 1;
	await page.getByRole('button', { name: 'Reload fee-tier' }).click();
	await expect(page.getByTestId('research-fee-source')).toContainText('stale');
	await expect(page.getByLabel('Maker fee rate')).toHaveValue('0.0025');
	await page.getByRole('button', { name: 'Refresh suggestion' }).click();
	await expect(page.getByLabel('Maker fee rate')).toHaveValue('0.0015');
});

test('the window hint names the strategy clock, not UTC hours', async ({ page }) => {
	const fiveMinute = { ...definition, timeframe: '5m' };
	await mockStrategy(page, {
		record: strategyRecord({ document: fiveMinute, strategy: fiveMinute, timeframe: '5m' })
	});
	await mockDatasets(page, '5m');
	await mockBacktestList(page);
	await page.goto(testStage);
	await page.getByTestId('run-advanced').locator('summary').click();
	await expect(page.getByText(/\(UTC, 5m bars\)/)).toBeVisible();
	await expect(page.getByText(/UTC hours/)).toHaveCount(0);
});

test('an invalid saved definition blocks backtests and studies with a reason', async ({ page }) => {
	await mockStrategy(page, { record: invalidRecord() });
	await mockBacktestList(page, () => [backtestEntry(fingerprint)]);
	await page.goto(testStage);
	const blocked = page.getByTestId('test-blocked-invalid');
	await expect(blocked).toContainText('Backtests and studies are blocked');
	await expect(blocked).toContainText('1 problem');
	await expect(page.getByRole('button', { name: 'Run backtest' })).toHaveCount(0);
	await expect(page.getByRole('button', { name: 'Run study' })).toHaveCount(0);
	// Existing results stay readable; with no current fingerprint their rules are unknown.
	await expect(page.locator('[data-rules="unknown"]')).toContainText('Rules unknown');
});

test('a strategy_invalid rejection from the server is explained, not retried', async ({ page }) => {
	await mockStrategy(page);
	await mockFees(page, suggestedFeeProfile());
	await mockDatasets(page);
	await mockBacktestList(page);
	let posts = 0;
	await page.route(
		(url) => url.pathname === '/api/v1/backtests',
		async (route) => {
			if (route.request().method() !== 'POST') return route.fallback();
			posts += 1;
			await route.fulfill({
				status: 422,
				json: {
					detail: {
						code: 'strategy_invalid',
						message: 'Strategy definition is invalid.',
						issues: [{ loc: 'entry', message: 'bad' }]
					}
				}
			});
		}
	);
	await page.goto(testStage);
	await page.getByRole('button', { name: 'Run backtest' }).click();
	await expect(page.getByRole('alert')).toContainText('The saved definition is not valid');
	expect(posts).toBe(1);
});

test('the run bar is one compact row with Advanced options and no engine picker', async ({
	page
}) => {
	await mockStrategy(page);
	await mockFees(page, suggestedFeeProfile());
	await mockDatasets(page);
	await mockBacktestList(page);
	const modelRequests: string[] = [];
	page.on('request', (request) => {
		const { pathname } = new URL(request.url());
		if (!pathname.startsWith('/api/')) return;
		if (pathname.includes(RETIRED_ENGINE_ROUTE) || pathname.includes('backtest-model'))
			modelRequests.push(pathname);
	});
	let launchBody: Record<string, unknown> | null = null;
	await page.route(
		(url) => url.pathname === '/api/v1/backtests',
		async (route) => {
			if (route.request().method() !== 'POST') return route.fallback();
			launchBody = (await route.request().postDataJSON()) as Record<string, unknown>;
			await route.fulfill({ status: 422, json: { detail: 'stop here in test' } });
		}
	);
	await page.setViewportSize({ width: 1440, height: 900 });
	await page.goto(testStage);
	const bar = page.getByTestId('run-bar');
	// One backtest model: no engine select or combobox in the run bar.
	await expect(bar.getByLabel('Engine')).toHaveCount(0);
	await expect(bar.getByRole('combobox', { name: /engine/i })).toHaveCount(0);
	await expect(bar.getByRole('option', { name: /engine/i })).toHaveCount(0);
	// Compact fields: dataset, period, capital, fees, then the actions on the right.
	await expect(bar.getByLabel('Verified 1h dataset')).toHaveValue(datasetFingerprint);
	await expect(bar.getByTestId('run-period')).toHaveText('Full coverage');
	await expect(bar.getByLabel('Initial capital (USDC)')).toBeVisible();
	await expect(bar.getByLabel('Maker fee rate')).toBeVisible();
	await expect(bar.getByRole('button', { name: 'Run backtest' })).toBeEnabled();
	const barBox = await bar.boundingBox();
	expect(barBox?.height ?? 999).toBeLessThan(90);

	// Advanced: slippage and custom dates hidden until opened; the summary keeps them visible.
	const advanced = page.getByTestId('run-advanced');
	await expect(advanced.locator('summary')).toContainText('slippage 10 bps');
	await expect(page.getByLabel('Fixed slippage (bps)')).toBeHidden();
	await advanced.locator('summary').click();
	await expect(page.getByLabel('Fixed slippage (bps)')).toBeVisible();
	await expect(page.getByLabel('Evaluation start')).toBeVisible();
	await page.getByLabel('Evaluation start').fill('2026-08-10T00:00');
	await expect(bar.getByTestId('run-period')).toContainText('2026-08-10 →');

	// Run a study is a disclosure button beside Run backtest.
	const study = page.getByRole('button', { name: /Run a study/ });
	await expect(study).toHaveAttribute('aria-expanded', 'false');
	await study.click();
	await expect(study).toHaveAttribute('aria-expanded', 'true');
	await expect(page.getByLabel('Study')).toBeVisible();

	await expect(page.getByRole('combobox', { name: /engine/i })).toHaveCount(0);

	await page.getByRole('button', { name: 'Run backtest' }).click();
	await expect.poll(() => launchBody).not.toBeNull();
	expect(launchBody).not.toHaveProperty(REMOVED_ENGINE_FIELD);
	// Blank spread stress is omitted.
	expect(launchBody).not.toHaveProperty('spread_bps');
	// The disclosure is static copy: the page never asks for a model description.
	expect(modelRequests).toEqual([]);
});

test('the Test stage explains how backtests simulate and never names engine variants', async ({
	page
}) => {
	await mockStrategy(page);
	await mockFees(page, suggestedFeeProfile());
	await mockDatasets(page);
	await mockBacktestList(page, () => [backtestEntry(fingerprint)]);
	await mockBacktestDetail(page, resultFingerprint, fingerprint);
	await page.goto(`${testStage}?result=${resultFingerprint}`);
	const result = page.getByTestId('workspace-result');
	await expect(result.getByText('Simulated result (candle-based fills)')).toBeVisible();

	const runBar = page.getByRole('region', { name: 'Run a backtest' });
	const disclosure = runBar.getByTestId('backtest-model-disclosure');
	await disclosure.locator('summary').click();
	await expect(disclosure).toContainText('Maker-limit entries rest');
	await expect(disclosure).toContainText('Unfilled entries expire');
	await expect(disclosure).toContainText('Time exit at close');
	await expect(disclosure).toContainText('Liquidation at the window end');
	await expect(disclosure).toContainText("Candles don't show queue position");
	await expect(disclosure).toContainText('(max_entry_wait_bars)');

	// Result detail discloses modeling limits; an unstressed run shows no spread copy.
	await expect(result.getByTestId('validity-limits')).toContainText('fill completely');
	await expect(result.getByTestId('spread-stress')).toHaveCount(0);
	await expect(result.getByTestId('backtest-model-disclosure')).toHaveCount(1);

	const text = await page.locator('main').innerText();
	expect(text).not.toMatch(/\bV[1-4]\b/);
	expect(text).not.toContain(RETIRED_ENGINE_PREFIX);
	expect(text).not.toMatch(/engine contract/i);
});
