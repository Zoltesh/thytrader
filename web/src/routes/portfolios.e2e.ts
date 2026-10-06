import { expect, test } from '../e2e/harness';
import {
	CORE,
	LAB,
	PROPOSAL,
	fillComparisonFixture,
	openBookFixture,
	SOL,
	labFixture,
	mockPortfolioApi,
	newPortfolioState,
	portfolioFixture,
	proposalFixture,
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

test('switches portfolios, offers Start, and leaves All bots in place', async ({ page }) => {
	await mockPortfolioApi(page, newPortfolioState([portfolioFixture(), labFixture()]));
	await page.goto('/deployments');
	await expect(page.getByRole('heading', { level: 1, name: 'Portfolio' })).toBeVisible();
	const card = page.getByTestId('portfolio-card');
	await expect(card).toContainText('Core');
	await expect(card.locator('.chip.live')).toHaveText('LIVE');
	await expect(card.getByTestId('portfolio-state')).toHaveText('Not deployed');
	await expect(card.getByTestId('portfolio-start')).toBeEnabled();
	await expect(card.getByTestId('portfolio-pause')).toBeDisabled();
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
	await expect(page.getByTestId('limits-note')).toContainText('stay latched until you reset them');
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
	await expect(page.getByTestId('manager-note')).toContainText('submits proposals');
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

test('starts a live portfolio only after the real-orders checkbox, then pauses and stops it', async ({
	page
}) => {
	const state = newPortfolioState([portfolioFixture()]);
	await mockPortfolioApi(page, state);
	await page.goto('/deployments');
	await page.getByTestId('portfolio-start').click();
	const dialog = page.getByTestId('portfolio-action-dialog');
	await expect(dialog).toContainText('Start portfolio “Core”?');
	await expect(dialog.getByTestId('start-lines')).toContainText('EMA Trend Pullback');
	await expect(dialog.getByTestId('start-lines')).toContainText('allocated');
	const confirm = dialog.getByRole('button', { name: 'Start live' });
	await expect(confirm).toBeDisabled();
	await dialog.getByTestId('portfolio-live-ack').check();
	await confirm.click();
	await expect
		.poll(() => calls(state, 'POST', `${CORE}/start`))
		.toEqual([{ revision: 3, i_understand_live: true }]);
	const card = page.getByTestId('portfolio-card');
	await expect(card.getByTestId('portfolio-state')).toHaveText('Running');
	await expect(card).toContainText('2 sleeves · Running');
	await expect(card.getByTestId('portfolio-start')).toBeDisabled();
	await expect(card.getByTestId('portfolio-start')).not.toHaveClass(/primary/);
	const bots = page.getByTestId('sleeve-bot');
	await expect(bots.first().getByRole('link', { name: 'Running' })).toHaveAttribute(
		'href',
		/\/deployments\/de/
	);

	await page.getByRole('button', { name: 'Pause sleeve RSI Reversion' }).click();
	await page.getByTestId('portfolio-action-dialog').getByRole('button', { name: 'Pause' }).click();
	await expect.poll(() => state.calls.some((call) => call.path.endsWith('/pause'))).toBe(true);
	await expect(card.getByTestId('portfolio-state')).toHaveText('Partly running');

	await card.getByTestId('portfolio-stop').click();
	const stop = page.getByTestId('portfolio-action-dialog');
	await stop.getByLabel(/Stop and flatten/).check();
	await stop.getByRole('button', { name: 'Stop and flatten' }).click();
	await expect
		.poll(() => state.calls.filter((call) => call.path.endsWith(`${CORE}/stop`)).length)
		.toBe(1);
	await expect(card.getByTestId('portfolio-state')).toHaveText('Stopped');
});

test('manager proposals: approve, decline, and Ask why opens the agent panel', async ({ page }) => {
	const state = newPortfolioState([portfolioFixture()]);
	state.proposals = [
		proposalFixture(),
		proposalFixture({
			proposal_id: '0d000000-0000-7000-8000-000000000002',
			kind: 'pause_sleeve',
			summary: 'Pause sleeve “RSI Reversion”.',
			change: { kind: 'pause_sleeve', sleeve_id: '5eee0000-0000-7000-8000-000000000002' },
			approval_reason: 'Pausing a sleeve needs approval: may_pause_sleeves is off.'
		})
	];
	await mockPortfolioApi(page, state);
	await page.goto(`/deployments?portfolio=${CORE}&tab=manager`);
	const cards = page.getByTestId('proposal-card');
	await expect(cards).toHaveCount(2);
	await expect(cards.first()).toContainText('EMA Trend Pullback 50% → 60%');
	await expect(cards.first()).toContainText('below its walk-forward range');
	await expect(cards.first()).toContainText('a rebalance moves real capital');

	await cards.first().getByRole('button', { name: 'Ask why' }).click();
	const panel = page.getByRole('complementary', { name: 'Agent' });
	await expect(panel).toBeVisible();
	await expect(panel.getByTestId('agent-context')).toContainText('Proposal: Rebalance');
	await expect(panel.getByTestId('chat-draft')).toHaveValue(new RegExp(`Proposal ${PROPOSAL}`));
	await page.getByRole('button', { name: 'Close agent' }).click();

	await cards.first().getByRole('button', { name: 'Approve…' }).click();
	const approve = page.getByTestId('approve-dialog');
	await approve.getByLabel(/Note/).fill('Agreed after the study.');
	await approve.getByRole('button', { name: 'Approve', exact: true }).click();
	await expect
		.poll(() => calls(state, 'POST', `${PROPOSAL}/approve`))
		.toEqual([{ note: 'Agreed after the study.' }]);
	await expect(page.getByTestId('proposal-card')).toHaveCount(1);
	await expect(page.getByTestId('decided-proposals')).toContainText('Approved');

	await page.getByTestId('proposal-card').getByRole('button', { name: 'Decline' }).click();
	await expect(page.getByTestId('proposal-card')).toHaveCount(0);
	await expect(page.getByTestId('no-proposals')).toBeVisible();
	await expect(page.getByTestId('decided-proposals')).toContainText('Declined');
});

test('a latched breaker blocks resume until it is reset on the Limits tab', async ({ page }) => {
	const state = newPortfolioState([portfolioFixture()]);
	state.bots = {
		'5eee0000-0000-7000-8000-000000000001': 'paused',
		'5eee0000-0000-7000-8000-000000000002': 'paused'
	};
	state.breakerLatched = true;
	await mockPortfolioApi(page, state);
	await page.goto(`/deployments?portfolio=${CORE}&tab=limits`);
	const card = page.getByTestId('portfolio-card');
	await expect(card.getByTestId('portfolio-breaker-chip')).toBeVisible();
	await expect(card.getByTestId('portfolio-resume')).toBeDisabled();
	const breaker = page.getByTestId('breaker-card');
	await expect(breaker.getByTestId('breaker-latched')).toHaveText('Max drawdown stop latched');
	await expect(breaker).toContainText('21% below its peak');
	await breaker.getByTestId('breaker-reset').click();
	await page
		.getByTestId('portfolio-action-dialog')
		.getByRole('button', { name: 'Reset breaker' })
		.click();
	await expect.poll(() => calls(state, 'POST', '/breaker/reset').length).toBe(1);
	await expect(breaker.getByTestId('breaker-clear')).toBeVisible();
	await expect(card.getByTestId('portfolio-resume')).toBeEnabled();
});

test('long portfolio names keep the header usable at 1440 px', async ({ page }) => {
	await page.setViewportSize({ width: 1440, height: 900 });
	const names = [
		'Trend core - daily EMA20/100 (11 majors)',
		'Trend core - daily EMA20/100 risk r0.05 (11 majors)',
		'Trend core - daily EMA20/100 risk r0.08 (11 majors)'
	];
	const rows = names.map((name, index) =>
		portfolioFixture({
			portfolio_id: `01a0f000-0000-7000-8000-00000000a${String(index).padStart(3, '0')}`,
			name,
			mode: 'paper'
		})
	);
	await mockPortfolioApi(page, newPortfolioState(rows));
	await page.goto('/deployments');
	await expect(page.getByTestId('portfolio-switch')).toHaveCount(3);
	const newButton = page.getByTestId('new-portfolio');
	await expect(newButton).toBeVisible();
	await expect(newButton).toHaveText('New portfolio…');
	const overflow = await page.evaluate(
		() => document.documentElement.scrollWidth - document.documentElement.clientWidth
	);
	expect(overflow).toBeLessThanOrEqual(0);
	const button = await newButton.boundingBox();
	expect(button).not.toBeNull();
	expect((button?.x ?? 0) + (button?.width ?? 0)).toBeLessThanOrEqual(1440);
	const lede = await page.locator('.page-head .lede').boundingBox();
	expect(lede?.height ?? 0).toBeLessThan(80);
	await expect(page.getByTestId('portfolio-switch').first()).toHaveAttribute('title', names[0]);
});

test('a sleeve worker stop is not shown as venue-resting protection', async ({ page }) => {
	const state = newPortfolioState([portfolioFixture()]);
	const ema = '5eee0000-0000-7000-8000-000000000001';
	state.bots[ema] = 'running';
	state.books[ema] = [
		openBookFixture({
			protection_status: 'covered',
			protection: {
				required_quantity: '0.0083',
				covered_quantity: '0.0083',
				uncovered_quantity: '0',
				stop_side: 'sell',
				stop_side_valid: true,
				stop_geometry_valid: true,
				mechanism: 'synthetic',
				venue_resting: false,
				worker_dependent: true,
				observed_at: null,
				verified_at: null,
				observation_source: 'synthetic_worker',
				freshness: 'unknown',
				evaluated_at: '2026-09-21T20:05:00+00:00',
				freshness_max_age_seconds: 120,
				geometry_basis: 'working_target',
				reasons: ['synthetic_worker_dependent']
			}
		})
	];
	await mockPortfolioApi(page, state);
	await page.goto('/deployments');
	const book = page.getByTestId('open-book');
	await expect(book.getByTestId('open-book-state')).toHaveText('Worker stop');
	await expect(book.getByTestId('protection-evidence')).toContainText('not venue-resting');
	await expect(book.getByTestId('open-book-state')).not.toHaveClass(/ok/);
});

test('sleeve rows show each open book and the paper vs live fill panel', async ({ page }) => {
	const state = newPortfolioState([portfolioFixture()]);
	const ema = '5eee0000-0000-7000-8000-000000000001';
	state.bots[ema] = 'running';
	state.bots['5eee0000-0000-7000-8000-000000000002'] = 'running';
	state.books[ema] = [openBookFixture({ entry_fees: '8', unrealized_pnl_net: '-0.41' })];
	state.fillComparisons = [fillComparisonFixture('deee0000-0000-7000-8000-000000000001')];
	await mockPortfolioApi(page, state);
	await page.goto('/deployments');
	const book = page.getByTestId('open-book');
	await expect(book).toHaveCount(1);
	await expect(book.getByTestId('open-book-state')).toHaveText('Protected · unverified');
	await expect(book.getByTestId('open-book-state')).not.toHaveClass(/ok/);
	await expect(book.getByTestId('open-book-pnl')).toHaveText('-0.41 USDC (net)');
	await expect(book.getByTestId('open-book-levels')).toContainText('60,125.5');
	await expect(book.getByTestId('open-book-levels')).toContainText('TP63,800');
	await expect(page.getByTestId('sleeve-twin')).toHaveText('Paper twin · fills');
	const panel = page.getByTestId('paper-live-fills');
	await expect(panel.getByRole('heading', { name: 'Paper vs live' })).toBeVisible();
	await expect(panel.getByTestId('plf-row')).toHaveCount(1);
	await expect(panel.getByTestId('plf-filled')).toHaveText(['4 of 6 filled', '5 of 5 filled']);
	await expect(panel.getByTestId('plf-slippage')).toHaveText(['0 bps', '+1.8 bps']);
	await expect(panel.getByTestId('plf-wait')).toHaveText(['1h 59m', '6s']);
	await expect(panel.getByTestId('plf-gap')).toHaveText('Live fills 1h 59m sooner (median).');
	await expect(panel.getByRole('link', { name: 'LIVE' })).toHaveAttribute(
		'href',
		'/deployments/deee0000-0000-7000-8000-000000000001'
	);
});

test('no twins means no paper vs live panel', async ({ page }) => {
	const state = newPortfolioState([portfolioFixture()]);
	state.bots['5eee0000-0000-7000-8000-000000000001'] = 'running';
	await mockPortfolioApi(page, state);
	await page.goto('/deployments');
	await expect(page.getByTestId('sleeve-row').first()).toBeVisible();
	await expect
		.poll(() => state.calls.some((call) => call.path.endsWith('/fill-comparisons')))
		.toBe(true);
	await expect(page.getByTestId('paper-live-fills')).toHaveCount(0);
	await expect(page.getByTestId('sleeve-twin')).toHaveCount(0);
});
