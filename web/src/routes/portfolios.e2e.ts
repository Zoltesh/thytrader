import { expect, test } from '../e2e/harness';
import {
	CORE,
	LAB,
	SOL,
	labFixture,
	mockPortfolioApi,
	newPortfolioState,
	portfolioFixture,
	type MockState
} from '../e2e/portfolio-fixtures';

function calls(state: MockState, method: string, suffix: string): unknown[] {
	return state.calls
		.filter((call) => call.method === method && call.path.endsWith(suffix))
		.map((call) => call.body);
}

test('empty state creates a portfolio from the dialog', async ({ page }) => {
	const state = newPortfolioState([]);
	await mockPortfolioApi(page, state);
	await page.goto('/deployments');
	await expect(page.getByTestId('portfolio-empty')).toContainText('No portfolios yet');
	await page.getByTestId('portfolio-empty').getByRole('button', { name: 'New portfolio…' }).click();
	const dialog = page.getByRole('dialog', { name: 'New portfolio' });
	await dialog.getByLabel('Name').fill('Swing');
	await dialog.getByRole('button', { name: 'Live' }).click();
	await expect(dialog.getByTestId('new-portfolio-live-note')).toBeVisible();
	await dialog.getByRole('button', { name: 'Paper' }).click();
	await dialog.getByLabel(/Capital/).fill('1000');
	await dialog.getByLabel('Cash reserve (%)').fill('10');
	await dialog.getByRole('button', { name: 'Create portfolio' }).click();
	await expect
		.poll(() => calls(state, 'POST', '/api/v1/portfolios'))
		.toEqual([
			{
				name: 'Swing',
				mode: 'paper',
				quote_currency: 'USDC',
				capital_quote: '1000',
				cash_reserve_fraction: '0.1'
			}
		]);
	await expect(page.getByTestId('portfolio-name')).toHaveText('Swing');
});

test('switches portfolios, keeps deploy disabled, and leaves All bots in place', async ({
	page
}) => {
	await mockPortfolioApi(page, newPortfolioState([portfolioFixture(), labFixture()]));
	await page.goto('/deployments');
	await expect(page.getByRole('heading', { level: 1, name: 'Portfolio' })).toBeVisible();
	const card = page.getByTestId('portfolio-card');
	await expect(card).toContainText('Core');
	await expect(card.locator('.chip.live')).toHaveText('LIVE');
	await expect(card.getByRole('button', { name: 'Deploy portfolio' })).toBeDisabled();
	await expect(card).toContainText('Deploying a portfolio arrives next');
	await expect(page.getByTestId('live-strip')).toHaveCount(0);
	await page.getByTestId('portfolio-switch').filter({ hasText: 'Lab' }).click();
	await expect(page.getByTestId('portfolio-name')).toHaveText('Lab');
	await expect(page).toHaveURL(new RegExp(`portfolio=${LAB}`));
	await page.getByRole('tab', { name: /Limits/ }).click();
	await expect(page).toHaveURL(/tab=limits/);
	await expect(page.getByRole('heading', { level: 2, name: 'All bots' })).toBeVisible();
	await expect(page.getByTestId('portfolio-filter')).toBeVisible();
	await expect(page.getByRole('link', { name: 'Start a deployment' }).first()).toHaveAttribute(
		'href',
		'/strategies'
	);
});

test('adds a sleeve, edits weights, and removes a sleeve with revision guards', async ({
	page
}) => {
	const state = newPortfolioState([portfolioFixture({ cash_reserve_fraction: '0.07' })]);
	await mockPortfolioApi(page, state);
	await page.goto('/deployments');
	await expect(page.getByTestId('sleeve-row')).toHaveCount(2);
	await expect(page.getByTestId('largest-asset')).toContainText('BTC 50% (limit 60%)');

	await page.getByRole('button', { name: '+ Add sleeve from a strategy' }).click();
	const picker = page.getByRole('dialog', { name: 'Add a sleeve' });
	await expect(picker.getByRole('radio', { name: /BTC dollar trend/ })).toBeDisabled();
	await expect(picker.getByRole('radio', { name: /EMA Trend Pullback/ })).toBeDisabled();
	await picker.getByLabel('Search strategies').fill('sol');
	await picker.getByRole('radio', { name: /SOL Breakout/ }).check();
	await picker.getByLabel('Weight (% of capital)').fill('10');
	await picker.getByRole('button', { name: 'Add sleeve' }).click();
	await expect
		.poll(() => calls(state, 'POST', '/sleeves'))
		.toEqual([{ revision: 3, strategy_id: SOL, weight_fraction: '0.1' }]);
	await expect(page.getByTestId('sleeve-row')).toHaveCount(3);

	await page.getByRole('button', { name: 'Edit weights' }).click();
	await page.getByLabel('Weight for EMA Trend Pullback (%)').fill('70');
	await expect(page.getByRole('button', { name: 'Save weights' })).toBeDisabled();
	await page.getByLabel('Weight for EMA Trend Pullback (%)').fill('40');
	await page.getByRole('button', { name: 'Save weights' }).click();
	await expect.poll(() => calls(state, 'PUT', '/weights').length).toBe(1);
	const sent = calls(state, 'PUT', '/weights')[0] as {
		revision: number;
		weights: { weight_fraction: string }[];
	};
	expect(sent.revision).toBe(4);
	expect(sent.weights.map((item) => item.weight_fraction)).toEqual(['0.4', '0.33', '0.1']);

	await page.getByRole('button', { name: 'Remove sleeve RSI Reversion' }).click();
	await page
		.getByRole('dialog', { name: 'Remove sleeve?' })
		.getByRole('button', { name: 'Remove sleeve' })
		.click();
	await expect(page.getByTestId('sleeve-row')).toHaveCount(2);
	const removal = state.calls.find((call) => call.method === 'DELETE');
	expect(removal?.path).toContain('/sleeves/5eee0000-0000-7000-8000-000000000002');
});

test('a stale weights save reloads the portfolio and says so', async ({ page }) => {
	const state = newPortfolioState([portfolioFixture()]);
	state.conflictOnWeights = true;
	await mockPortfolioApi(page, state);
	await page.goto('/deployments');
	await page.getByRole('button', { name: 'Edit weights' }).click();
	await page.getByLabel('Weight for EMA Trend Pullback (%)').fill('45');
	await page.getByRole('button', { name: 'Save weights' }).click();
	await expect(page.getByRole('alert')).toContainText('Changed elsewhere; reloaded — try again.');
});

test('runs a portfolio backtest with polling and shows the combined result', async ({ page }) => {
	const state = newPortfolioState([portfolioFixture()]);
	await mockPortfolioApi(page, state);
	await page.goto(`/deployments?portfolio=${CORE}&tab=backtest`);
	await expect(page.getByLabel('Maker fee rate')).toHaveValue('0.004');
	await expect(page.getByLabel('Taker fee rate')).toHaveValue('0.006');
	await page.getByRole('button', { name: 'Run portfolio backtest' }).click();
	await expect
		.poll(() => calls(state, 'POST', '/backtests'))
		.toEqual([
			{ revision: 3, maker_fee_rate: '0.004', taker_fee_rate: '0.006', fixed_slippage_bps: '10' }
		]);
	const metrics = page.getByTestId('backtest-metrics');
	await expect(metrics).toContainText('+11.80%', { timeout: 20_000 });
	await expect(metrics).toContainText('-4.80%');
	await expect(page.getByTestId('contribution-table')).toContainText('+9.20 pts');
	await expect(page.getByTestId('contribution-table')).toContainText(
		'Cash (reserve and unallocated)'
	);
	await expect(page.getByTestId('correlation-table')).toContainText('0.20');
	await expect(page.getByTestId('backtest-disclosures')).toContainText(
		'portfolio-level caps and cross-sleeve interactions are not simulated'
	);
	await expect(page.getByRole('img', { name: /equity/i })).toBeVisible();
});

test('a rejected portfolio backtest lists each sleeve problem', async ({ page }) => {
	const state = newPortfolioState([portfolioFixture()]);
	state.rejectBacktest = true;
	await mockPortfolioApi(page, state);
	await page.goto(`/deployments?portfolio=${CORE}&tab=backtest`);
	await page.getByRole('button', { name: 'Run portfolio backtest' }).click();
	const error = page.getByTestId('backtest-error');
	await expect(error).toContainText('Some sleeves have no usable datasets.');
	await expect(error).toContainText('RSI Reversion');
	await expect(error).toContainText('ETH-USDC 1h');
});

test('edits limits and manager settings and shows the journal', async ({ page }) => {
	const state = newPortfolioState([portfolioFixture()]);
	await mockPortfolioApi(page, state);
	await page.goto(`/deployments?portfolio=${CORE}&tab=limits`);
	await expect(page.getByTestId('limit-per-asset')).toContainText('60% of capital');
	await expect(page.getByTestId('limits-note')).toContainText('today no order passes through them');
	await page.getByRole('button', { name: 'Edit limits' }).click();
	await page.getByLabel('Max per asset (% of capital)').fill('50');
	await page.getByLabel(/Daily loss stop/).fill('');
	await page.getByRole('button', { name: 'Save limits' }).click();
	await expect
		.poll(() => calls(state, 'PATCH', CORE))
		.toEqual([
			{
				revision: 3,
				limits: {
					max_total_exposure_fraction: '0.83',
					max_per_asset_fraction: '0.5',
					daily_loss_quote: null,
					max_drawdown_fraction: '0.2'
				}
			}
		]);

	await page.getByRole('tab', { name: /Manager/ }).click();
	await expect(page.getByTestId('manager-note')).toContainText('proposals arrive next');
	await expect(page.getByTestId('manager-never')).toContainText('Place orders itself');
	await expect(page.getByTestId('portfolio-journal')).toContainText(
		'Changed weights: EMA Trend Pullback 40% → 50%.'
	);
	await page.getByRole('button', { name: 'Edit', exact: true }).click();
	await page.getByLabel(/Mandate/).fill('Keep 20% in USDC.');
	await page.getByLabel('Run research and propose new sleeves').check();
	await page.getByRole('button', { name: 'Save manager settings' }).click();
	await expect.poll(() => calls(state, 'PATCH', CORE).length).toBe(2);
	expect(calls(state, 'PATCH', CORE)[1]).toEqual({
		revision: 4,
		manager: {
			mandate: 'Keep 20% in USDC.',
			permissions: {
				may_rebalance: true,
				max_weight_change_per_week: '0.1',
				may_pause_sleeves: true,
				may_propose_sleeves: true
			}
		}
	});
});

test('portfolio storage outage keeps the bot list working', async ({ page }) => {
	const state = newPortfolioState([]);
	state.unavailable = true;
	await mockPortfolioApi(page, state);
	await page.goto('/deployments');
	await expect(page.getByTestId('portfolio-unavailable')).toContainText(
		'Portfolios are unavailable right now.'
	);
	await expect(page.getByRole('button', { name: 'New portfolio…' })).toBeDisabled();
	await expect(page.getByText('No bots yet')).toBeVisible();
});
