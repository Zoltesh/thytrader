import { describe, expect, it } from 'vitest';
import {
	fillCountText,
	fillShare,
	outcomeText,
	portfolioSide,
	slippageText,
	waitGapText,
	waitText,
	type EntryFillDigest,
	type PaperLiveFillComparison
} from './fill-comparison';

function digest(overrides: Partial<EntryFillDigest> = {}): EntryFillDigest {
	return {
		deployment_id: 'd',
		portfolio_id: null,
		status: 'running',
		entries_rested: 4,
		entries_filled: 3,
		entries_expired: 1,
		entries_rejected: 0,
		entries_working: 0,
		average_fill_vs_limit_bps: '0',
		average_seconds_to_fill: '5',
		median_seconds_to_fill: '5',
		...overrides
	};
}

const row: PaperLiveFillComparison = {
	strategy_fingerprint: 'sha256:x',
	strategy_id: null,
	strategy_name: 'ETH trend',
	product_id: 'ETH-USDC',
	paper: digest({ deployment_id: 'p', portfolio_id: 'pf', median_seconds_to_fill: '7195' }),
	live: digest({ deployment_id: 'l', median_seconds_to_fill: '5' })
};

describe('paper vs live fill readings (ADR 0098)', () => {
	it('reads fill rate and outcomes', () => {
		expect(fillShare(digest())).toBe(0.75);
		expect(fillShare(digest({ entries_rested: 0, entries_filled: 0 }))).toBeNull();
		expect(fillCountText(digest())).toBe('3 of 4 filled');
		expect(fillCountText(digest({ entries_rested: 0 }))).toBe('No entries yet');
		expect(outcomeText(digest({ entries_rejected: 2, entries_working: 1 }))).toBe(
			'1 expired · 2 rejected · 1 working'
		);
	});

	it('signs slippage so positive is worse than the limit', () => {
		expect(slippageText(digest({ average_fill_vs_limit_bps: '1.25' }))).toEqual({
			text: '+1.3 bps',
			tone: 'neg'
		});
		expect(slippageText(digest({ average_fill_vs_limit_bps: '-12.4' }))).toEqual({
			text: '-12 bps',
			tone: 'pos'
		});
		expect(slippageText(digest())).toEqual({ text: '0 bps', tone: 'muted' });
		expect(slippageText(digest({ average_fill_vs_limit_bps: null }))).toBeNull();
	});

	it('formats waits and the gap between the twins', () => {
		expect(waitText('5')).toBe('5s');
		expect(waitText('130')).toBe('2m 10s');
		expect(waitText('7195')).toBe('1h 59m');
		expect(waitText(null)).toBe('—');
		expect(waitGapText(row)).toBe('Live fills 1h 59m sooner (median).');
		expect(waitGapText({ ...row, live: digest({ median_seconds_to_fill: null }) })).toBeNull();
		expect(portfolioSide(row, 'pf')).toBe('paper');
		expect(portfolioSide(row, 'other')).toBeNull();
	});
});
