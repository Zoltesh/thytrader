/**
 * Holdings row actions (ADR 0124): sell an unmanaged coin to USDC, or start a live strategy
 * bot that adopts it. Both are live-only and need a connected Coinbase account; quote
 * currencies (cash) and demo balances get no actions.
 */
import { isAdoptableAsset } from '$lib/adoption';

export type HoldingAction = 'sell' | 'adopt';

/** The USDC product a "Sell to USDC" of ``currency`` trades. */
export function sellProduct(currency: string): string {
	return `${currency.toUpperCase()}-USDC`;
}

/** Whether a holdings row offers the sell and adopt actions. */
export function holdingActionsAvailable(demo: boolean, currency: string): boolean {
	return !demo && isAdoptableAsset(currency);
}

/** Dialog title for one action on one coin. */
export function holdingActionTitle(action: HoldingAction, currency: string): string {
	return action === 'sell' ? `Sell ${currency} to USDC?` : `Adopt ${currency} into a live bot?`;
}
