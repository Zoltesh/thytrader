/**
 * Typed client for the advisory readiness preflight (ADR 0114).
 *
 * Amounts stay decimal strings. Unknown caps are null, never invented zeros.
 * The report does not change risk policy.
 */

export type ReadinessSeverity = 'info' | 'advisory' | 'violation' | 'unknown';

export interface ReadinessFinding {
	reason_code: string;
	severity: ReadinessSeverity;
	detail: string;
	deployment_id?: string | null;
	portfolio_id?: string | null;
}

export interface ReadinessAccountCaps {
	quote_currency: string;
	venue_available_quote: string | null;
	capital_base: string | null;
	current_exposure: string;
	effective_exposure_cap: string | null;
	remaining_entry_capacity: string | null;
	absolute_exposure_cap: string | null;
	enforcement: 'advisory_only';
}

export interface ReadinessFeeEvidence {
	demo: boolean;
	unavailable_reason: 'demo_or_missing_credentials' | 'read_failure' | null;
	account_maker_fee_rate: string | null;
	account_taker_fee_rate: string | null;
	optimistic_books: readonly { deployment_id: string }[];
}

export interface ReadinessReport {
	schema_version: string;
	report_kind: 'readiness';
	overall_status: 'healthy' | 'degraded' | 'failed';
	payload: {
		scope: 'deployment' | 'portfolio' | 'fleet';
		note: string;
		account: ReadinessAccountCaps | null;
		fee_evidence: ReadinessFeeEvidence;
		findings: readonly ReadinessFinding[];
	};
}

export interface ReadinessHeadline {
	tone: 'ok' | 'warn' | 'error';
	summary: string;
}

/** One-line operator summary. Violations outrank unknown, which outranks advisory. */
export function readinessHeadline(report: ReadinessReport): ReadinessHeadline {
	const findings = report.payload.findings;
	const violation = findings.find((item) => item.severity === 'violation');
	if (violation) return { tone: 'error', summary: violation.detail };
	const unknown = findings.find((item) => item.severity === 'unknown');
	if (unknown) return { tone: 'warn', summary: unknown.detail };
	const advisory = findings.find((item) => item.severity === 'advisory');
	if (advisory) return { tone: 'warn', summary: advisory.detail };
	const account = report.payload.account;
	if (account?.remaining_entry_capacity) {
		return {
			tone: 'ok',
			summary: `About ${account.remaining_entry_capacity} ${account.quote_currency} of account entry capacity remains. Advisory only.`
		};
	}
	return { tone: 'ok', summary: 'No readiness findings. This check does not change policy.' };
}

export async function fetchReadiness(): Promise<ReadinessReport> {
	const response = await fetch('/api/v1/operator/readiness', {
		headers: { Accept: 'application/json' }
	});
	if (!response.ok) {
		throw new Error('Readiness preflight is temporarily unavailable.');
	}
	return (await response.json()) as ReadinessReport;
}
