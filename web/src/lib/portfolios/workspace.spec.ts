import { describe, expect, it } from 'vitest';
import type { PortfolioActionResponse, SleeveOutcome } from '$lib/portfolios-types';
import { outcomeNotice } from './workspace';

function sleeve(
	name: string,
	outcome: SleeveOutcome['outcome'],
	message: string | null = null
): SleeveOutcome {
	return {
		sleeve_id: null,
		strategy_id: null,
		strategy_name: name,
		outcome,
		deployment_id: null,
		message
	};
}

function result(
	action: PortfolioActionResponse['action'],
	outcomes: SleeveOutcome[]
): PortfolioActionResponse {
	return { action, outcomes, deployment: {} as PortfolioActionResponse['deployment'] };
}

describe('outcomeNotice', () => {
	it('counts the changed sleeves', () => {
		expect(outcomeNotice(result('pause', [sleeve('A', 'paused'), sleeve('B', 'unchanged')]))).toBe(
			'Paused 1 sleeve.'
		);
	});

	it('shows the warning a sleeve started or resumed with (ADR 0142)', () => {
		const notice = outcomeNotice(
			result('start', [
				sleeve('Trend', 'started', 'HISTORY_TOO_SHORT: the bot waits.'),
				sleeve('Range', 'started')
			])
		);
		expect(notice).toBe('Started 2 sleeves. Trend: HISTORY_TOO_SHORT: the bot waits.');
	});

	it('lists failures after warnings', () => {
		const notice = outcomeNotice(
			result('resume', [
				sleeve('Trend', 'resumed', 'WARMUP_POLICY_MISSING: x.'),
				sleeve('Range', 'failed', 'WARMUP_POLICY_REQUIRED: y.')
			])
		);
		expect(notice).toBe(
			'Resumed 1 sleeve. Trend: WARMUP_POLICY_MISSING: x. 1 could not: Range (WARMUP_POLICY_REQUIRED: y.).'
		);
	});
});
