import { DEFAULT_CONTEXT_LABEL } from '$lib/workstation-chrome';
import type { LayoutLoad } from './$types';

/**
 * Default topbar pill for pages that do not override contextLabel.
 *
 * This is a static environment label, not a health or connectivity signal.
 */
export const load: LayoutLoad = (): { contextLabel: string } => {
	return { contextLabel: DEFAULT_CONTEXT_LABEL };
};
