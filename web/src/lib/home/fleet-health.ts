/**
 * Home fleet entry banner (ADR 0130) from `GET /api/v1/operator/fleet-health`.
 *
 * The report says, per mode and quote scope, whether the entry gate would admit any new
 * entry right now (`entries_admissible`), and names the books and records that block it.
 * Home shows a prominent banner when live or paper entries are blocked fleet-wide or
 * unknown, and a quieter one for systemic blockers in recent bot decisions. A failed read
 * is shown as unchecked, never as healthy.
 */

export type Admissibility = 'yes' | 'blocked' | 'unknown';

export interface FleetBlockingBook {
	deployment_id: string;
	status: 'running' | 'paused' | 'stopped';
	product_id: string;
	detail: string;
}

export interface FleetEntryCheck {
	check: string;
	status: 'pass' | 'blocked' | 'unknown' | 'not_applicable';
	reason_code: string | null;
	blocker_class: 'evidence' | 'latch' | 'policy' | 'capacity' | 'transient' | 'operator' | null;
	fleet_wide: boolean;
	detail: string;
	deployments: FleetBlockingBook[];
}

export interface FleetEntryScope {
	mode: 'paper' | 'live';
	scope: string;
	entries_admissible: Admissibility;
	reason_codes: string[];
	blocking_deployment_ids: string[];
	running_deployments: number;
	occupied_deployments: number;
	alert_subject: string;
	checks: FleetEntryCheck[];
}

export interface FleetSystemicBlocker {
	kind: 'evidence_block' | 'shared_reason' | 'sizing_skips' | 'warmup_stuck';
	outcome: 'entry_blocked' | 'skipped';
	reason_code: string;
	rows: number;
	deployments: number;
	deployment_ids: string[];
	detail: string;
}

export interface FleetHealthReport {
	overall_status: 'healthy' | 'degraded' | 'failed';
	payload: {
		entries: {
			evaluated_at: string;
			complete: boolean;
			detail: string;
			live_entries_admissible: Admissibility;
			paper_entries_admissible: Admissibility;
			scopes: FleetEntryScope[];
		};
		decisions: {
			storage: 'available' | 'unavailable';
			window_hours: number;
			systemic: FleetSystemicBlocker[];
		};
	};
}

/** One blocking book line with a link target. */
export interface BannerBook {
	deploymentId: string;
	label: string;
	detail: string;
}

/** One blocked or unknown scope as Home shows it. */
export interface BannerScope {
	key: string;
	title: string;
	reasons: string;
	books: BannerBook[];
	clears: string;
}

export interface FleetBanner {
	tone: 'danger' | 'warn';
	title: string;
	scopes: BannerScope[];
	systemic: string[];
}

/** Blocks that clear by themselves or were set on purpose: shown, never in the danger tone. */
const SELF_CLEARING = new Set(['capacity', 'transient', 'operator']);

const CLEARS: Record<string, string> = {
	evidence: 'Clears only when the named record is repaired.',
	latch: 'Clears after an operator breaker reset.',
	policy:
		'Clears when manual futures no longer hold the shared collateral or a reserve covers them.',
	capacity: 'Clears when an exit frees an open-position slot or exposure room.',
	transient: 'Clears by itself when the entry window slides.',
	operator: 'Clears after an explicit fleet rearm.'
};

/** Failed reads must never look like an unblocked fleet. */
export async function fetchFleetHealth(): Promise<FleetHealthReport> {
	const response = await fetch('/api/v1/operator/fleet-health', {
		headers: { Accept: 'application/json' }
	});
	if (!response.ok) throw new Error(`Fleet entry health unavailable (HTTP ${response.status}).`);
	return (await response.json()) as FleetHealthReport;
}

function modeLabel(mode: 'paper' | 'live'): string {
	return mode === 'live' ? 'Live' : 'Paper';
}

function scopeView(scope: FleetEntryScope): BannerScope {
	const blocking = scope.checks.filter(
		(check) => check.fleet_wide && (check.status === 'blocked' || check.status === 'unknown')
	);
	const unknown = scope.checks.filter((check) => check.status === 'unknown');
	const shown = blocking.length > 0 ? blocking : unknown;
	const books = shown.flatMap((check) =>
		check.deployments.map((book) => ({
			deploymentId: book.deployment_id,
			label: `${book.product_id} (${book.status})`,
			detail: book.detail
		}))
	);
	const classes = [...new Set(blocking.map((check) => check.blocker_class ?? ''))].filter(
		(item) => item !== ''
	);
	const blocked = scope.entries_admissible === 'blocked';
	return {
		key: scope.alert_subject,
		title: `${modeLabel(scope.mode)} ${scope.scope} entries ${blocked ? 'are blocked' : 'cannot be confirmed'} fleet-wide`,
		reasons: blocked
			? scope.reason_codes.join(', ')
			: unknown.map((check) => check.check).join(', ') + ' could not be evaluated',
		books,
		clears: classes.map((item) => CLEARS[item] ?? '').join(' ')
	};
}

/**
 * The banner to show, or null when every occupied scope admits entries and no systemic
 * decision blocker was seen. Evidence blocks, and live latch or policy blocks, use the danger
 * tone; a fully invested fleet or a full clustering window is a warning.
 */
export function fleetBanner(report: FleetHealthReport): FleetBanner | null {
	const entries = report.payload.entries;
	const scopes = entries.scopes.filter((scope) => scope.entries_admissible !== 'yes');
	const systemic =
		report.payload.decisions.storage === 'available'
			? report.payload.decisions.systemic.map((item) => `${item.reason_code}: ${item.detail}`)
			: [];
	if (!entries.complete) {
		return {
			tone: 'warn',
			title: 'Fleet entry readiness is unknown',
			scopes: [],
			systemic: [entries.detail || 'The fleet could not be read.', ...systemic]
		};
	}
	if (scopes.length === 0 && systemic.length === 0) return null;
	const danger = scopes.some((scope) =>
		scope.checks.some(
			(check) =>
				check.fleet_wide &&
				check.status === 'blocked' &&
				(check.blocker_class === 'evidence' ||
					(scope.mode === 'live' &&
						check.blocker_class !== null &&
						!SELF_CLEARING.has(check.blocker_class)))
		)
	);
	const blockedCount = scopes.filter((scope) => scope.entries_admissible === 'blocked').length;
	return {
		tone: danger ? 'danger' : 'warn',
		title:
			blockedCount > 0
				? 'New entries are blocked fleet-wide'
				: scopes.length > 0
					? 'Fleet entry readiness is unknown'
					: `Systemic entry blockers in the last ${report.payload.decisions.window_hours} h`,
		scopes: scopes.map(scopeView),
		systemic
	};
}
