/**
 * Truthful protection badges (ADR 0112). A legacy `covered` / `open_protected`
 * reading is not green when the evidence says the stop is synthetic, partial,
 * or not a confirmed venue order.
 */
import { positionStateLabel } from '$lib/deployments';
import { compareDecimalStrings } from '$lib/portfolio';

/** Quantitative stop cover from a deployment position or sleeve book. */
export type ProtectionEvidence = {
	required_quantity: string;
	covered_quantity: string;
	uncovered_quantity: string;
	stop_side: string | null;
	stop_side_valid: boolean;
	stop_geometry_valid: boolean;
	mechanism: 'venue' | 'synthetic' | 'none' | 'unverified' | string;
	venue_resting: boolean;
	worker_dependent: boolean;
	/** Null means the observation time is unknown. */
	observed_at: string | null;
	/** Null means the stop was not verified at a known time. */
	verified_at: string | null;
	reasons: readonly string[];
};

export type ProtectionBadge = {
	text: string;
	tone: 'ok' | 'warn' | 'bad' | 'muted';
	title: string;
	detail: string;
};

type BadgeBook = {
	position_state?: string | null;
	protection_status?: string | null;
	target_price?: string | null;
	protection?: ProtectionEvidence | null;
};

type BadgeTone = ProtectionBadge['tone'];

/** Short chip or the longer bot-detail sentence when no evidence is present. */
export function protectionBadge(
	book: BadgeBook,
	options: { fallback?: 'chip' | 'sentence' } = {}
): ProtectionBadge {
	const evidence = book.protection;
	if (evidence) return evidenceBadge(book, evidence);
	const fallback = options.fallback ?? 'chip';
	const text =
		fallback === 'sentence'
			? (positionStateLabel(book.position_state, {
					hasTarget: book.target_price != null && book.target_price !== ''
				}) ??
				book.protection_status ??
				'unknown')
			: legacyChip(book.position_state).text;
	return {
		text,
		tone: legacyChip(book.position_state).tone,
		title: text,
		detail: ''
	};
}

function legacyChip(state: string | null | undefined): { text: string; tone: BadgeTone } {
	switch (state) {
		case 'open_protected':
			return { text: 'Protected', tone: 'ok' };
		case 'open_unprotected':
			return { text: 'Unprotected', tone: 'bad' };
		case 'open_unverified':
			return { text: 'Unverified', tone: 'warn' };
		case 'exiting':
			return { text: 'Exiting', tone: 'warn' };
		case 'entering':
			return { text: 'Entering', tone: 'muted' };
		default:
			return { text: 'Open', tone: 'muted' };
	}
}

function evidenceBadge(book: BadgeBook, evidence: ProtectionEvidence): ProtectionBadge {
	const detail = evidenceDetail(evidence);
	const title = `${detail}. ${evidence.reasons.join(', ') || 'no reason reported'}.`;
	if (evidence.worker_dependent || evidence.mechanism === 'synthetic') {
		return { text: 'Worker stop', tone: 'warn', title, detail };
	}
	if (venueCoverConfirmed(book, evidence)) {
		const text = book.target_price ? 'Venue TP/SL' : 'Venue stop';
		return { text, tone: 'ok', title, detail };
	}
	if (book.protection_status === 'unknown' || evidence.mechanism === 'unverified') {
		return { text: 'Unverified', tone: 'warn', title, detail };
	}
	return { text: 'Unprotected', tone: 'bad', title, detail };
}

function venueCoverConfirmed(book: BadgeBook, evidence: ProtectionEvidence): boolean {
	return (
		book.protection_status === 'covered' &&
		evidence.mechanism === 'venue' &&
		evidence.venue_resting &&
		evidence.stop_side_valid &&
		evidence.stop_geometry_valid &&
		compareDecimalStrings(evidence.uncovered_quantity, '0') === 0
	);
}

function evidenceDetail(evidence: ProtectionEvidence): string {
	const qty = `${evidence.covered_quantity} of ${evidence.required_quantity}`;
	if (evidence.worker_dependent || evidence.mechanism === 'synthetic') {
		return `${qty} worker-dependent · not venue-resting · time unknown`;
	}
	if (evidence.venue_resting && evidence.verified_at) {
		return `${qty} venue stop · verified ${evidence.verified_at}`;
	}
	if (evidence.venue_resting) {
		return `${qty} venue stop · verified time unknown`;
	}
	const when = evidence.observed_at ?? 'time unknown';
	return `${qty} · ${evidence.mechanism} · ${when}`;
}
