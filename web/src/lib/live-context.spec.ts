import { describe, expect, it } from 'vitest';
import { LiveContextRegistry, liveStripText } from './live-context';

describe('liveStripText', () => {
	it('names the bot, the market with its own quote, and a known cap', () => {
		expect(liveStripText({ kind: 'bot', productId: 'ETH-USDC', cap: '100.00 USDC' })).toBe(
			'this bot places real Coinbase orders · ETH / USDC · allocated 100.00 USDC'
		);
	});

	it('omits an unknown cap instead of guessing', () => {
		expect(liveStripText({ kind: 'bot', productId: 'UNI-USD', cap: null })).toBe(
			'this bot places real Coinbase orders · UNI / USD'
		);
	});

	it('words an order ticket and a live arm differently', () => {
		expect(liveStripText({ kind: 'order', productId: 'BTC-USDC', cap: null })).toBe(
			'this order will be sent to Coinbase with real money · BTC / USDC'
		);
		expect(liveStripText({ kind: 'arm', productId: null, cap: null })).toBe(
			'arming this strategy places real Coinbase orders'
		);
	});
});

describe('LiveContextRegistry', () => {
	it('is empty until something declares a live context', () => {
		expect(new LiveContextRegistry().current).toBeNull();
	});

	it('shows the newest declaration and falls back when it is released', () => {
		const registry = new LiveContextRegistry();
		const releaseBot = registry.declare({ kind: 'bot', productId: 'ETH-USDC', cap: null });
		const releaseArm = registry.declare({ kind: 'arm', productId: 'BTC-USDC', cap: null });
		expect(registry.current?.kind).toBe('arm');
		releaseArm();
		expect(registry.current?.kind).toBe('bot');
		releaseBot();
		expect(registry.current).toBeNull();
	});

	it('releasing an older declaration never clears a newer one', () => {
		const registry = new LiveContextRegistry();
		const first = registry.declare({ kind: 'order', productId: 'BTC-USDC', cap: null });
		registry.declare({ kind: 'order', productId: 'ETH-USDC', cap: null });
		first();
		expect(registry.current?.productId).toBe('ETH-USDC');
		first();
		expect(registry.current?.productId).toBe('ETH-USDC');
	});
});
