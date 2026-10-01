/**
 * Plain-language description of ThyTrader's single backtest model (ADR 0083).
 *
 * Mirrors the assumptions agents read from `GET /api/v1/research/backtest-model`
 * so the browser can render the same disclosure without a request. There is one
 * model; nothing here selects between simulation variants.
 */

/** The unversioned identity every published result carries in `result.engine`. */
export const BACKTEST_ENGINE = 'thytrader-backtest';
export type BacktestEngine = typeof BACKTEST_ENGINE;

export type BacktestAssumptionKey =
	| 'signal_timing'
	| 'maker_entries'
	| 'unfilled_entries'
	| 'stops_and_targets'
	| 'time_exit'
	| 'evaluation_end'
	| 'fees'
	| 'slippage'
	| 'spread_stress'
	| 'shorts'
	| 'multi_instrument'
	| 'pyramiding'
	| 'queue_position';

export type BacktestAssumption = {
	key: BacktestAssumptionKey;
	label: string;
	detail: string;
};

export const BACKTEST_MODEL_HONESTY =
	'Backtests are simulated results from historical candles. They are research evidence and not a promise of paper or live performance.';

/** Describe the entry wait with the strategy's own value when it is known. */
function entryWaitText(maxEntryWaitBars: number | null): string {
	if (maxEntryWaitBars === null || !Number.isInteger(maxEntryWaitBars) || maxEntryWaitBars < 1) {
		return 'max_entry_wait_bars candles';
	}
	const unit = maxEntryWaitBars === 1 ? 'candle' : 'candles';
	return `${maxEntryWaitBars} ${unit} (max_entry_wait_bars)`;
}

/**
 * Every fill and cost assumption a backtest result is simulated under.
 *
 * `maxEntryWaitBars` substitutes the strategy's entry wait into the unfilled-entry
 * assumption; omit it to name the setting instead.
 */
export function backtestModelAssumptions(
	maxEntryWaitBars: number | null = null
): BacktestAssumption[] {
	return [
		{
			key: 'signal_timing',
			label: 'Signals on completed candles',
			detail:
				'Entry conditions, HTF filters, and per-indicator timeframes are evaluated only on completed candles; no future bar influences a signal.'
		},
		{
			key: 'maker_entries',
			label: 'Maker-limit entries rest',
			detail:
				"A matched signal rests a post-only limit at that candle's close. A later candle fills it only if its low (high for shorts) trades through the limit, at the limit price with the maker fee and no slippage."
		},
		{
			key: 'unfilled_entries',
			label: 'Unfilled entries expire',
			detail: `An entry rests up to ${entryWaitText(maxEntryWaitBars)}, then cancels or reprices at that candle's close, as the strategy's unfilled-entry policy says.`
		},
		{
			key: 'stops_and_targets',
			label: 'Stops and targets on bar extremes',
			detail:
				'On the fill candle only the stop can trigger (stop first); the take-profit rests from the next candle. On every candle the stop is checked first: if a candle touches both the stop and the take-profit, the stop is assumed, because a candle cannot show which traded first (paper does the same). Otherwise a touched take-profit fills at the target. Stops fill as takers at the stop or the worse open on a gap.'
		},
		{
			key: 'time_exit',
			label: 'Time exit at close',
			detail: "A position held max_bars_held candles sells at that candle's close."
		},
		{
			key: 'evaluation_end',
			label: 'Liquidation at the window end',
			detail: 'Open inventory is sold at the open of the candle at evaluation end.'
		},
		{
			key: 'fees',
			label: 'Maker and taker fees',
			detail:
				'Resting entries and take-profits pay the maker fee; stop, time, and end-of-window exits pay the taker fee. Rates are modeled inputs, not observed Coinbase fees.'
		},
		{
			key: 'slippage',
			label: 'Fixed taker slippage',
			detail: 'fixed_slippage_bps moves every taker exit against the position.'
		},
		{
			key: 'spread_stress',
			label: 'Optional spread stress',
			detail:
				'spread_bps (default 0) is a constant total bid-ask spread stress. Taker exits cross half of it, and stop triggers and open-position marks use the stressed bid (ask for shorts). Maker fills stay at their limit. It is a stress input, not observed order-book data.'
		},
		{
			key: 'shorts',
			label: 'Spot shorts are synthetic',
			detail:
				'Short strategies sell to open and buy to cover against quote cash; there is no borrow, margin, or funding model.'
		},
		{
			key: 'multi_instrument',
			label: 'One shared quote balance',
			detail:
				'Multi-instrument strategies process products in product_id order on each candle against one quote balance.'
		},
		{
			key: 'pyramiding',
			label: 'Same-side adds',
			detail:
				'Pyramiding adds rest as maker limits and average into the open position without moving its stop or target.'
		},
		{
			key: 'queue_position',
			label: "Candles don't show queue position",
			detail:
				'A touched limit is assumed to fill completely. Real resting orders can miss or partially fill when the price only touches them.'
		}
	];
}

/** Modeling-limit codes every result discloses in `summary.validity_limits`. */
export type ValidityLimitCode =
	'maker_touch_full_fill' | 'stop_before_tp_same_bar' | 'spot_short_synthetic';

const VALIDITY_LIMIT_TEXT: Record<ValidityLimitCode, string> = {
	maker_touch_full_fill: 'A touched maker limit is assumed to fill completely (no queue position).',
	stop_before_tp_same_bar:
		'When one candle touches both the stop and the take-profit, the stop is assumed (conservative).',
	spot_short_synthetic: 'Spot shorts are synthetic: no borrow, margin, or funding is modeled.'
};

/** Plain text for one disclosed modeling limit; unknown codes are shown verbatim. */
export function formatValidityLimit(code: string): string {
	return code in VALIDITY_LIMIT_TEXT
		? VALIDITY_LIMIT_TEXT[code as ValidityLimitCode]
		: `Modeling limit: ${code}`;
}
