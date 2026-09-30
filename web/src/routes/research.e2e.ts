/**
 * Test stage of the strategy workspace (absorbs /research and, per strategy,
 * /backtests) plus the /research redirect and chooser.
 */
import { expect, test } from '../e2e/harness';
import {
	backtestEntry,
	datasetFingerprint,
	draft,
	fingerprint,
	fingerprintV2,
	historyVersion,
	mockBacktestDetail,
	mockBacktestList,
	mockDatasets,
	mockFees,
	mockStrategy,
	resultFingerprint,
	strategyId,
	suggestedFeeProfile
} from '../e2e/workspace-fixtures';

const twoVersions = [historyVersion(1, fingerprint), historyVersion(2, fingerprintV2)];
const testStage = `/strategies/${strategyId}/test`;

test('old /research links redirect to the Test stage with the exact version', async ({ page }) => {
	await mockStrategy(page, { versions: twoVersions });
	await mockBacktestList(page);
	await mockDatasets(page);
	await page.goto(`/research?strategy=${strategyId}&strategy_fingerprint=${fingerprint}`);
	await expect(page).toHaveURL(
		new RegExp(`/strategies/${strategyId}/test\\?version=${encodeURIComponent(fingerprint)}`)
	);
	await expect(page.getByTestId('workspace-version-pill')).toHaveText(/Published v1/);
	await page.goto(`/research?strategy=${strategyId}`);
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

test('runs a backtest for the selected version and inspects the result inline', async ({
	page
}) => {
	await mockStrategy(page, { versions: twoVersions });
	await mockFees(page, suggestedFeeProfile());
	await mockDatasets(page);
	const v2Result = `sha256:${'9'.repeat(64)}`;
	await mockBacktestList(page, (requested) =>
		requested === fingerprintV2
			? [
					backtestEntry(fingerprintV2, {
						result_fingerprint: v2Result,
						engine_contract_version: 'thytrader-bar-backtest-v2'
					})
				]
			: [
					backtestEntry(fingerprint, {
						published_at: '2026-09-22T10:00:00Z',
						summary: { ...backtestEntry(fingerprint).summary, total_return_fraction: '0.11' }
					})
				]
	);
	await mockBacktestDetail(page, v2Result, fingerprintV2);
	let launchBody: Record<string, unknown> | null = null;
	await page.route(
		(url) => url.pathname === '/api/v1/backtests',
		async (route) => {
			if (route.request().method() !== 'POST') return route.fallback();
			launchBody = (await route.request().postDataJSON()) as Record<string, unknown>;
			await route.fulfill({
				status: 201,
				json: { run_fingerprint: `sha256:${'e'.repeat(64)}`, result_fingerprint: v2Result }
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
	await expect(page.getByRole('option', { name: 'V3 — resting maker limit' })).toBeAttached();
	// Custom dates, slippage, and spread stress live behind the Advanced disclosure.
	await expect(page.getByText(/\(UTC, 1h bars\)/)).toBeHidden();
	await page.getByTestId('run-advanced').locator('summary').click();
	await expect(page.getByText(/\(UTC, 1h bars\)/)).toBeVisible();

	// Results list covers every published version of this strategy.
	const results = page.getByRole('table', { name: 'Backtest results for this strategy' });
	await expect(results.getByRole('row')).toHaveCount(3);
	await expect(results.getByRole('cell', { name: 'v1', exact: true })).toBeVisible();
	await expect(results.getByRole('cell', { name: 'v2', exact: true })).toBeVisible();
	await page.getByLabel('Only v2').check();
	await expect(results.getByRole('row')).toHaveCount(2);

	await page.getByLabel('Engine').selectOption('thytrader-bar-backtest-v2');
	await page.getByLabel('Constant spread (bps, total bid-ask)').fill('8');
	await page.getByRole('button', { name: 'Run backtest' }).click();
	await expect.poll(() => launchBody).not.toBeNull();
	expect(launchBody).toMatchObject({
		strategy_fingerprint: fingerprintV2,
		engine_contract_version: 'thytrader-bar-backtest-v2',
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
	// Compact result header: version · period · engine, plus the simulated-result chip.
	const head = result.getByTestId('backtest-result-head');
	await expect(head).toContainText(/v2 · \d{4}-\d{2}-\d{2} → \d{4}-\d{2}-\d{2} · \d+ bars · V1/);
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
	await page.goto(`${testStage}?version=${fingerprint}&result=${resultFingerprint}`);
	await expect(
		page.getByTestId('workspace-result').getByText('Simulated result (candle-based fills)')
	).toBeVisible();
	await expect(page.getByRole('link', { name: /Inspect v1 result/ })).toHaveAttribute(
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

test('a composed study runs from the Run a study disclosure against the exact version', async ({
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
	await page.getByLabel('Engine').selectOption('thytrader-bar-backtest-v1');
	await page.getByLabel('Study').selectOption('oos_holdout');
	await page.getByRole('button', { name: 'Run study' }).click();
	await expect.poll(() => studyBody).not.toBeNull();
	expect(studyBody).toMatchObject({ kind: 'oos_holdout', strategy_fingerprint: fingerprint });
	await expect(page.getByRole('alert')).toContainText('study rejected in test');
});

test('loads every result page for an exact version', async ({ page }) => {
	await mockStrategy(page);
	await mockDatasets(page);
	const offsets: number[] = [];
	await page.route(
		(url) => url.pathname === '/api/v1/backtests' && url.searchParams.has('strategy_fingerprint'),
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
	await page.route(`**/api/v1/strategies/${strategyId}/history`, (route) =>
		route.fulfill({
			json: {
				strategy_id: strategyId,
				latest_version: 1,
				next_version: 2,
				versions: [historyVersion(1, fingerprint)],
				draft: null
			}
		})
	);
	await page.route('**/api/v1/strategies/source/*', (route) =>
		route.fulfill({ json: { strategy: { ...draft, status: 'published', timeframe: '5m' } } })
	);
	await mockDatasets(page, '5m');
	await mockBacktestList(page);
	await page.goto(testStage);
	await page.getByTestId('run-advanced').locator('summary').click();
	await expect(page.getByText(/\(UTC, 5m bars\)/)).toBeVisible();
	await expect(page.getByText(/UTC hours/)).toHaveCount(0);
});

test('a draft-only strategy has nothing to test yet', async ({ page }) => {
	await mockStrategy(page, { versions: [], draft });
	await page.goto(testStage);
	await expect(page.getByRole('heading', { name: 'No published version yet' })).toBeVisible();
	await expect(page.getByRole('button', { name: 'Run backtest' })).toHaveCount(0);
});

test('the run bar is one compact row with Advanced options and a defaulted engine', async ({
	page
}) => {
	await mockStrategy(page);
	await mockFees(page, suggestedFeeProfile());
	await mockDatasets(page);
	await mockBacktestList(page);
	await page.route('**/api/v1/research/engine-support', (route) =>
		route.fulfill({
			json: {
				contract_version: 'thytrader-engine-support-v2',
				engines: [
					'thytrader-bar-backtest-v1',
					'thytrader-bar-backtest-v2',
					'thytrader-bar-backtest-v3',
					'thytrader-bar-backtest-v4'
				],
				rows: []
			}
		})
	);
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
	// Newest engine this launcher offers (V4 stays CLI-only here), not an empty select.
	await expect(page.getByLabel('Engine')).toHaveValue('thytrader-bar-backtest-v3');
	// Compact fields: dataset, period, capital, fees, engine, then the actions on the right.
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

	await page.getByRole('button', { name: 'Run backtest' }).click();
	await expect.poll(() => launchBody).not.toBeNull();
	expect(launchBody).toMatchObject({ engine_contract_version: 'thytrader-bar-backtest-v3' });
});

test('an unreadable engine-support matrix keeps the explicit engine choice', async ({ page }) => {
	await mockStrategy(page);
	await mockFees(page, suggestedFeeProfile());
	await mockDatasets(page);
	await mockBacktestList(page);
	await page.route('**/api/v1/research/engine-support', (route) =>
		route.fulfill({ status: 503, json: { detail: 'unavailable' } })
	);
	await page.goto(testStage);
	await expect(page.getByLabel('Verified 1h dataset')).toHaveValue(datasetFingerprint);
	await expect(page.getByLabel('Engine')).toHaveValue('');
	await expect(page.getByRole('button', { name: 'Run backtest' })).toBeDisabled();
});
