import { SETTINGS_CONTEXT_LABEL } from '$lib/workstation-chrome';
import type { LayoutLoad } from './$types';

/**
 * Settings keeps a distinct context pill for YAML knobs and write-only Coinbase credentials.
 */
export const load: LayoutLoad = (): { contextLabel: string } => {
	return { contextLabel: SETTINGS_CONTEXT_LABEL };
};
