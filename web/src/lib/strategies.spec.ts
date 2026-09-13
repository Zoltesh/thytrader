import { describe, expect, it } from 'vitest';
import {
	archiveConfirmMessage,
	PAPER_LIVE_STATUS_LEGEND,
	PAPER_LIVE_STATUS_TITLE,
	paperLiveStatusLabel,
	paperLiveStatusTitle,
	researchWindowHint
} from './strategies';

describe('researchWindowHint', () => {
	const bounds = { min: '2026-06-03T02:00', max: '2026-07-31T23:00' };

	it('names 1h bars instead of UTC hours', () => {
		expect(researchWindowHint(bounds, 50, '1h')).toBe(
			'Usable window for this dataset: 2026-06-03 02:00 → 2026-07-31 23:00 (UTC, 1h bars). It must fit inside the dataset with 50 warmup bars before it and one candle after it.'
		);
	});

	it('names 5m bars for five-minute strategies', () => {
		expect(researchWindowHint(bounds, 50, '5m')).toContain('(UTC, 5m bars)');
		expect(researchWindowHint(bounds, 50, '5m')).not.toContain('UTC hours');
	});

	it('falls back to 1h when timeframe is blank', () => {
		expect(researchWindowHint(bounds, 0, '  ')).toContain('(UTC, 1h bars)');
	});
});

describe('archiveConfirmMessage', () => {
	it('names the strategy, version, and fingerprint being archived', () => {
		const fingerprint = `sha256:${'a'.repeat(64)}`;
		const message = archiveConfirmMessage({
			name: 'Recovered BTC trend draft',
			latest_version: 1,
			latest_fingerprint: fingerprint
		});
		expect(message).toContain('Recovered BTC trend draft');
		expect(message).toContain('Version: v1');
		expect(message).toContain(`Fingerprint: ${fingerprint}`);
		expect(message.indexOf('Version: v1')).toBeLessThan(message.indexOf('This hides'));
		expect(message).toContain('hides the latest published fingerprint from active selection');
		expect(message).not.toContain('delete');
	});
});

describe('paper/live library column copy', () => {
	it('lists the real status tokens in the column legend', () => {
		expect(PAPER_LIVE_STATUS_LEGEND).toBe('unavailable · running · paused · stopped');
		expect(PAPER_LIVE_STATUS_TITLE).toContain('unavailable = no runtime');
		expect(PAPER_LIVE_STATUS_TITLE).toContain('paused = halted (protective exits continue)');
	});

	it('renders paper then live and explains both tokens', () => {
		const paperLive = { paper: 'running', live: 'unavailable' };
		expect(paperLiveStatusLabel(paperLive)).toBe('running / unavailable');
		expect(paperLiveStatusTitle(paperLive)).toBe(
			'Paper: running. Live: unavailable. Opens Deploy.'
		);
	});
});
