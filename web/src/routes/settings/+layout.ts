import { SETTINGS_CONTEXT_LABEL } from '$lib/workstation-chrome';
import type { LayoutLoad } from './$types';

/**
 * Settings keeps a distinct context pill. It is not a Coinbase secrets form.
 */
export const load: LayoutLoad = (): { contextLabel: string } => {
	return { contextLabel: SETTINGS_CONTEXT_LABEL };
};
