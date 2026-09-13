/**
 * Typed API client for Coinbase fee tiers and transaction cost profile.
 */

export interface FeeProfile {
	taker_fee_rate: string;
	maker_fee_rate: string;
	usd_volume_30d: string;
	fee_tier: string;
	as_of: string;
	source: 'coinbase';
}

export interface FeeErrorResponse {
	detail?: {
		code: string;
		message: string;
	};
}

/**
 * Format `FeeProfile.as_of` for the fees panel, or null when it is absent or invalid.
 */
export function formatFeeProfileAsOf(asOf: string): string | null {
	if (asOf.trim() === '') {
		return null;
	}
	const milliseconds = Date.parse(asOf);
	if (!Number.isFinite(milliseconds)) {
		return null;
	}
	return new Date(milliseconds).toLocaleString();
}

export async function fetchFeeProfile(): Promise<FeeProfile> {
	const response = await fetch('/api/v1/fees', {
		headers: { Accept: 'application/json' }
	});

	if (!response.ok) {
		let message = 'Fee profile is temporarily unavailable.';
		try {
			const errorBody = (await response.json()) as FeeErrorResponse;
			if (errorBody.detail?.message) {
				message = errorBody.detail.message;
			}
		} catch {
			// fallback
		}
		throw new Error(message);
	}

	return (await response.json()) as FeeProfile;
}
