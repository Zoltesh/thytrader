import { expect, test } from '../e2e/harness';

test('trade ticket places a paper long through the discretionary HTTP contract', async ({
	page
}) => {
	await page.route('**/api/v1/memory/trade-reasons**', async (route) => {
		if (route.request().method() !== 'GET') {
			await route.fulfill({ status: 405, json: { detail: 'read-only' } });
			return;
		}
		await route.fulfill({
			json: {
				schema_version: 'thytrader-trade-reason-v1',
				trade_reasons: [
					{
						schema_version: 'thytrader-trade-reason-v1',
						id: '01985cf0-7b60-7000-8000-000000000021',
						created_at: '2026-09-16T12:00:00Z',
						origin: 'human',
						intent_id: '01985cf0-7b60-7000-8000-000000000022',
						deployment_id: '01985cf0-7b60-7000-8000-0000000000aa',
						deployment_kind: 'discretionary',
						mode: 'paper',
						product_id: 'BTC-USD',
						purpose: 'entry',
						side: 'buy',
						strategy: null,
						signal: {
							kind: 'discretionary',
							last_signal: 'discretionary',
							candle_starts_at: '2026-09-16T12:00:00Z',
							timeframe: '5m'
						},
						risk: {
							decision: 'allow',
							reason_code: 'ALLOWED',
							detail: 'Admitted after risk.',
							policy_fingerprint: `sha256:${'b'.repeat(64)}`,
							policy_source: 'compiled_default'
						},
						notes: [
							{
								origin: 'human',
								body: 'Scale-in after HTF confirm',
								recorded_at: '2026-09-16T12:00:00Z'
							}
						],
						reconcile: {
							order_id: null,
							order_status: 'filled',
							filled_quantity: '0.01',
							reject_reason: null,
							unknown_timeout: false,
							ledger_available: true,
							fills: []
						}
					}
				]
			}
		});
	});
	await page.route('**/api/v1/discretionary-orders', async (route) => {
		if (route.request().method() !== 'POST') {
			await route.fulfill({ status: 405, json: { detail: 'method not allowed' } });
			return;
		}
		const body = route.request().postDataJSON() as {
			origin?: string;
			mode?: string;
			side?: string;
			stop_price?: string;
			take_profit_price?: string;
			idempotency_key?: string;
			maker_fee_rate?: string;
			taker_fee_rate?: string;
			note?: string;
		};
		expect(body.origin).toBe('human');
		expect(body.mode).toBe('paper');
		expect(body.side ?? 'long').toBe('long');
		expect(body.stop_price).toBe('90000');
		expect(body.take_profit_price).toBe('120000');
		expect(body.maker_fee_rate).toBe('0.001');
		expect(body.taker_fee_rate).toBe('0.002');
		expect(body.idempotency_key).toBeTruthy();
		expect(body.note).toBe('Scale-in after HTF confirm');
		await route.fulfill({
			json: {
				id: '01985cf0-7b60-7000-8000-0000000000aa',
				strategy_fingerprint: null,
				strategy_id: null,
				kind: 'discretionary',
				timeframe: '5m',
				product_id: 'BTC-USD',
				mode: 'paper',
				status: 'running',
				phase: 'pending_entry',
				cash: '10000',
				paper_starting_cash: '10000',
				last_evaluated_bar: null,
				last_signal: 'discretionary',
				mismatch_detail: null,
				pending_entry_bars: 0,
				bars_held: 0,
				created_at: '2026-09-15T00:00:00+00:00',
				updated_at: '2026-09-15T00:00:00+00:00',
				position: null,
				orders: [],
				fills: []
			}
		});
	});

	await page.goto('/trade');
	const nav = page.getByRole('navigation', { name: 'Primary navigation' });
	await expect(nav.getByRole('link', { name: 'Trade' })).toHaveAttribute('aria-current', 'page');
	const ticket = page.getByTestId('discretionary-ticket');
	await expect(ticket).toHaveAttribute('data-hydrated', 'true');
	await ticket.getByRole('textbox', { name: 'Limit price' }).fill('100000');
	await ticket.getByRole('textbox', { name: 'Quantity' }).fill('0.01');
	await ticket.getByRole('textbox', { name: 'Stop loss' }).fill('90000');
	await ticket.getByRole('textbox', { name: 'Take profit' }).fill('120000');
	await expect(ticket.getByRole('textbox', { name: 'Stop loss' })).toHaveValue('90000');
	await ticket.getByTestId('discretionary-note').fill('Scale-in after HTF confirm');
	// Review aside: entry, loss at stop, and reward:risk from the ticket's own numbers.
	const review = page.getByTestId('trade-review');
	await expect(review.getByTestId('review-entry')).toHaveText('100,000.00 USD post-only limit');
	await expect(review.getByTestId('review-max-loss')).toHaveText('100.00 USD before fees');
	await expect(review.getByTestId('review-reward-risk')).toHaveText('2.00');
	await expect(page.getByTestId('live-strip')).toHaveCount(0);
	await review.getByRole('button', { name: 'Place paper long' }).click();
	await expect(page.getByTestId('discretionary-result')).toContainText('pending_entry');
	await expect(page.getByTestId('trade-reason-review')).toContainText('Scale-in after HTF confirm');
});

test('live mode turns on the live chrome and sends only after the real-orders checkbox', async ({
	page
}) => {
	await page.route('**/api/v1/memory/trade-reasons**', (route) =>
		route.fulfill({ json: { trade_reasons: [] } })
	);
	await page.route('**/api/v1/risk-policy', (route) =>
		route.fulfill({
			json: {
				source: 'published',
				policy_fingerprint: `sha256:${'7'.repeat(64)}`,
				version: 7,
				quote_currency: 'USDC',
				allocations: []
			}
		})
	);
	const posted: Record<string, unknown>[] = [];
	await page.route('**/api/v1/discretionary-orders', async (route) => {
		posted.push(route.request().postDataJSON() as Record<string, unknown>);
		await route.fulfill({
			json: {
				id: '01985cf0-7b60-7000-8000-0000000000bb',
				strategy_fingerprint: null,
				strategy_id: null,
				kind: 'discretionary',
				timeframe: '5m',
				product_id: 'BTC-USDC',
				mode: 'live',
				status: 'running',
				phase: 'pending_entry',
				cash: '0',
				paper_starting_cash: null,
				last_evaluated_bar: null,
				last_signal: 'discretionary',
				mismatch_detail: null,
				pending_entry_bars: 0,
				bars_held: 0,
				created_at: '2026-09-15T00:00:00+00:00',
				updated_at: '2026-09-15T00:00:00+00:00',
				position: null,
				orders: [],
				fills: []
			}
		});
	});
	await page.goto('/trade');
	const ticket = page.getByTestId('discretionary-ticket');
	await expect(ticket).toHaveAttribute('data-hydrated', 'true');
	await ticket.getByRole('textbox', { name: 'Product' }).fill('BTC-USDC');
	await ticket.getByRole('textbox', { name: 'Limit price' }).fill('63380');
	await ticket.getByRole('textbox', { name: 'Quote notional' }).fill('50');
	await ticket.getByRole('textbox', { name: 'Stop loss' }).fill('61800');
	await ticket.getByRole('textbox', { name: 'Take profit' }).fill('66900');

	const review = page.getByTestId('trade-review');
	await expect(review).not.toHaveClass(/live/);
	await ticket.getByTestId('trade-mode').getByRole('button', { name: 'Live' }).click();
	await expect(
		ticket.getByTestId('trade-mode').getByRole('button', { name: 'Live' })
	).toHaveAttribute('aria-pressed', 'true');
	// Live chrome: strip under the top bar, inset frame, amber review aside.
	await expect(page.getByTestId('live-strip')).toHaveText(
		'LIVE: this order will be sent to Coinbase with real money · BTC / USDC'
	);
	await expect(page.locator('[data-live-frame="true"]')).toHaveCount(1);
	await expect(review).toHaveClass(/live/);
	await expect(review.getByTestId('review-max-loss')).toHaveText('1.25 USDC before fees');
	await expect(review.getByTestId('review-reward-risk')).toHaveText('2.23');
	await expect(review.getByTestId('review-risk-policy')).toContainText('Published v7');
	// Paper assumptions disappear in live mode; live keeps venue-recorded fees.
	await expect(ticket.getByRole('textbox', { name: 'Paper cash' })).toHaveCount(0);

	await review.getByRole('button', { name: 'Review live order…' }).click();
	let dialog = page.getByRole('dialog', { name: 'Send live order?' });
	await expect(dialog).toContainText('never borrow');
	await expect(dialog).toContainText('reconciles by client order id instead of re-sending');
	const send = dialog.getByRole('button', { name: 'Send live long' });
	await expect(send).toBeDisabled();
	await dialog.getByRole('button', { name: 'Cancel' }).click();
	expect(posted).toHaveLength(0);

	await review.getByRole('button', { name: 'Review live order…' }).click();
	dialog = page.getByRole('dialog', { name: 'Send live order?' });
	await expect(dialog.getByRole('button', { name: 'Send live long' })).toBeDisabled();
	await dialog
		.getByLabel('I understand this places real orders on Coinbase with real money.')
		.check();
	await dialog.getByRole('button', { name: 'Send live long' }).click();
	await expect.poll(() => posted.length).toBe(1);
	expect(posted[0]).toMatchObject({
		mode: 'live',
		product_id: 'BTC-USDC',
		quote_notional: '50',
		i_understand_live: true
	});
	expect(posted[0]).not.toHaveProperty('maker_fee_rate');
	await expect(page.getByTestId('discretionary-result')).toContainText('live');

	// Back to paper: the live chrome goes away.
	await ticket.getByTestId('trade-mode').getByRole('button', { name: 'Paper' }).click();
	await expect(page.getByTestId('live-strip')).toHaveCount(0);
	await expect(page.locator('[data-live-frame="true"]')).toHaveCount(0);
});

test('client-side ticket validation still blocks before any request or dialog', async ({
	page
}) => {
	let posts = 0;
	await page.route('**/api/v1/discretionary-orders', async (route) => {
		posts += 1;
		await route.fulfill({ status: 500, json: { detail: 'should not be called' } });
	});
	await page.goto('/trade');
	const ticket = page.getByTestId('discretionary-ticket');
	await expect(ticket).toHaveAttribute('data-hydrated', 'true');
	await ticket.getByTestId('trade-mode').getByRole('button', { name: 'Live' }).click();
	await page.getByRole('button', { name: 'Review live order…' }).click();
	await expect(page.getByRole('alert')).toContainText(
		'Provide exactly one of quantity or quote notional.'
	);
	await expect(page.getByRole('dialog', { name: 'Send live order?' })).toHaveCount(0);
	expect(posts).toBe(0);
});
