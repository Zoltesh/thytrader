export type Money = {
	amount: string;
	currency: 'USD';
};

export type PortfolioAsset = {
	currency: string;
	name: string;
	available: string;
	hold: string;
	total: string;
	value: Money | null;
};

export type Portfolio = {
	as_of: string;
	connection: {
		provider: 'coinbase';
		status: 'connected' | 'demo';
		permissions: string[];
	};
	demo: boolean;
	total_value: Money;
	assets: PortfolioAsset[];
	unvalued_assets: string[];
};

export type ApiError = {
	detail?: {
		code?: string;
		message?: string;
	};
};

export function formatUsd(amount: string): string {
	return new Intl.NumberFormat('en-US', {
		style: 'currency',
		currency: 'USD',
		minimumFractionDigits: 2,
		maximumFractionDigits: 2
	}).format(Number(amount));
}

export function permissionLabel(permission: string): string {
	return permission.charAt(0).toUpperCase() + permission.slice(1).toLowerCase();
}
