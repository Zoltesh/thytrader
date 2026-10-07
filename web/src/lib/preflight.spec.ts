import { describe, expect, it } from 'vitest';

import { readinessHeadline, type ReadinessReport } from './preflight';

function report(findings: ReadinessReport['payload']['findings']): ReadinessReport {
	return {
		schema_version: 'thytrader-operator-report-v1',
		report_kind: 'readiness',
		overall_status: 'degraded',
		payload: {
			scope: 'fleet',
			note: 'advisory',
			account: {
				quote_currency: 'USDC',
				venue_available_quote: '320',
				capital_base: '320',
				current_exposure: '0',
				effective_exposure_cap: '80',
				remaining_entry_capacity: '80',
				absolute_exposure_cap: '80',
				enforcement: 'advisory_only'
			},
			fee_evidence: {
				demo: false,
				unavailable_reason: null,
				account_maker_fee_rate: '0.005',
				account_taker_fee_rate: '0.009',
				optimistic_books: []
			},
			findings
		}
	};
}

describe('readinessHeadline', () => {
	it.each(['failed', 'degraded'] as const)(
		'does not show Clear for %s with no findings',
		(status) => {
			const partial = report([]);
			partial.overall_status = status;
			const headline = readinessHeadline(partial);
			expect(headline.tone).toBe('warn');
			expect(headline.summary).toContain('not confirmed');
			expect(headline.summary).not.toContain('80');
		}
	);

	it('shows capacity only for healthy complete evidence', () => {
		const complete = report([]);
		complete.overall_status = 'healthy';
		expect(readinessHeadline(complete).summary).toContain('80 USDC');
	});

	it('names an allocation overcommitment as a warning, not a violation', () => {
		const headline = readinessHeadline(
			report([
				{
					reason_code: 'ALLOCATION_OVERCOMMITMENT',
					severity: 'advisory',
					detail: '8 live book(s) commit 320 USDC against cap 80 USDC.'
				}
			])
		);
		expect(headline.tone).toBe('warn');
		expect(headline.summary).toContain('320');
	});

	it('prefers an actual exposure violation over an advisory', () => {
		const headline = readinessHeadline(
			report([
				{
					reason_code: 'ALLOCATION_OVERCOMMITMENT',
					severity: 'advisory',
					detail: 'allocations exceed the cap'
				},
				{
					reason_code: 'ACCOUNT_EXPOSURE_CAP_EXCEEDED',
					severity: 'violation',
					detail: 'exposure 100 exceeds cap 80'
				}
			])
		);
		expect(headline.tone).toBe('error');
		expect(headline.summary).toContain('100');
	});

	it('does not treat unknown venue capacity as remaining room', () => {
		const headline = readinessHeadline(
			report([
				{
					reason_code: 'VENUE_BALANCE_UNKNOWN',
					severity: 'unknown',
					detail: 'account caps are unknown'
				}
			])
		);
		expect(headline.tone).toBe('warn');
		expect(headline.summary).toContain('unknown');
	});
});
