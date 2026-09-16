import { describe, expect, it } from 'vitest';
import {
	archiveConfirmMessage,
	latestDatasets,
	datasetEvaluationWindow,
	INDICATOR_KIND_OPTIONS,
	operandChoices,
	PAPER_LIVE_STATUS_LEGEND,
	PAPER_LIVE_STATUS_TITLE,
	paperLiveStatusLabel,
	paperLiveStatusTitle,
	parseIndicatorOperandKey,
	researchWindowHint,
	serializeIndicator,
	unboundIndicatorTimeframes,
	validHtfTimeframes
} from './strategies';

describe('latestDatasets', () => {
	it('keeps one latest revision per product and timeframe', () => {
		const datasets = [
			{
				product_id: 'BTC-USD',
				timeframe: '1h',
				starts_at: '2026-01-01T00:00:00Z',
				ends_at: '2026-02-01T00:00:00Z',
				content_fingerprint: 'sha256:old1h'
			},
			{
				product_id: 'BTC-USD',
				timeframe: '1h',
				starts_at: '2026-01-01T00:00:00Z',
				ends_at: '2026-03-01T00:00:00Z',
				content_fingerprint: 'sha256:new1h'
			},
			{
				product_id: 'BTC-USD',
				timeframe: '1d',
				starts_at: '2026-01-01T00:00:00Z',
				ends_at: '2026-03-01T00:00:00Z',
				content_fingerprint: 'sha256:1d'
			}
		];
		const latest = latestDatasets(datasets);
		expect(latest.map((dataset) => dataset.content_fingerprint).sort()).toEqual([
			'sha256:1d',
			'sha256:new1h'
		]);
	});
});

describe('validHtfTimeframes', () => {
	it('allows coarser integer multiples only', () => {
		expect(validHtfTimeframes('1m')).toEqual([
			'5m',
			'15m',
			'30m',
			'1h',
			'2h',
			'4h',
			'6h',
			'1d'
		]);
		expect(validHtfTimeframes('5m')).toEqual(['15m', '30m', '1h', '2h', '4h', '6h', '1d']);
		expect(validHtfTimeframes('1h')).toEqual(['2h', '4h', '6h', '1d']);
		expect(validHtfTimeframes('15m')).toEqual(['30m', '1h', '2h', '4h', '6h', '1d']);
		expect(validHtfTimeframes('4h')).toEqual(['1d']);
		expect(validHtfTimeframes('3h')).toEqual([]);
	});
});

describe('unboundIndicatorTimeframes', () => {
	it('omits the decision clock and HTF-filter clock', () => {
		expect(
			unboundIndicatorTimeframes(
				[
					{ id: 'ema_fast', kind: 'ema', input: 'close', parameters: { period: 20 } },
					{
						id: 'hour_sma',
						kind: 'sma',
						input: 'close',
						timeframe: '1h',
						parameters: { period: 20 }
					},
					{
						id: 'day_sma',
						kind: 'sma',
						input: 'close',
						timeframe: '1d',
						parameters: { period: 20 }
					}
				],
				'5m',
				'1h'
			)
		).toEqual(['1d']);
	});
});

describe('datasetEvaluationWindow', () => {
	const dataset = {
		product_id: 'BTC-USD',
		timeframe: '1m',
		starts_at: '2026-07-10T00:00:00Z',
		ends_at: '2026-07-10T01:00:00Z',
		content_fingerprint: 'sha256:1m'
	};

	it('spaces the usable window by the strategy bar duration', () => {
		expect(datasetEvaluationWindow(dataset, 2, '1m')).toEqual({
			min: '2026-07-10T00:02',
			max: '2026-07-10T00:59'
		});
		expect(datasetEvaluationWindow(dataset, 1, '15m')).toEqual({
			min: '2026-07-10T00:15',
			max: '2026-07-10T00:45'
		});
	});
});

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

describe('indicator kind picker', () => {
	it('lists shipped kinds including MACD and Bollinger series', () => {
		expect(INDICATOR_KIND_OPTIONS.map((option) => option.kind)).toEqual([
			'ema',
			'sma',
			'rsi',
			'atr',
			'volume_sma',
			'highest',
			'lowest',
			'stdev',
			'roc',
			'williams_r',
			'cci',
			'wma',
			'momentum',
			'mfi',
			'macd',
			'bollinger',
			'identity',
			'constant'
		]);
		expect(INDICATOR_KIND_OPTIONS.map((option) => option.kind)).toContain('macd');
	});

	it('serializes identity without period and constant without input', () => {
		expect(
			serializeIndicator({
				id: 'px',
				kind: 'identity',
				input: 'close',
				parameters: {}
			})
		).toEqual({ id: 'px', kind: 'identity', input: 'close', parameters: {} });
		expect(
			serializeIndicator({
				id: 'rsi_level',
				kind: 'constant',
				parameters: { value: '40' }
			})
		).toEqual({ id: 'rsi_level', kind: 'constant', parameters: { value: '40' } });
	});

	it('expands MACD and Bollinger into series operand choices', () => {
		expect(
			operandChoices([
				{
					id: 'trend_macd',
					kind: 'macd',
					input: 'close',
					parameters: { fast_period: 12, slow_period: 26, signal_period: 9 }
				},
				{ id: 'ema_fast', kind: 'ema', input: 'close', parameters: { period: 20 } }
			]).map((choice) => choice.key)
		).toEqual([
			'indicator:trend_macd.macd',
			'indicator:trend_macd.signal',
			'indicator:trend_macd.histogram',
			'indicator:ema_fast',
			'literal'
		]);
		expect(parseIndicatorOperandKey('indicator:trend_macd.histogram')).toEqual({
			indicator: 'trend_macd',
			series: 'histogram'
		});
		expect(parseIndicatorOperandKey('indicator:ema_fast')).toEqual({ indicator: 'ema_fast' });
	});

	it('serializes MACD periods and Bollinger multiplier', () => {
		expect(
			serializeIndicator({
				id: 'trend_macd',
				kind: 'macd',
				input: 'close',
				parameters: { fast_period: 12, slow_period: 26, signal_period: 9 }
			})
		).toEqual({
			id: 'trend_macd',
			kind: 'macd',
			input: 'close',
			parameters: { fast_period: 12, slow_period: 26, signal_period: 9 }
		});
		expect(
			serializeIndicator({
				id: 'bands',
				kind: 'bollinger',
				input: 'close',
				parameters: { period: 20, stdev_multiplier: '2' }
			})
		).toEqual({
			id: 'bands',
			kind: 'bollinger',
			input: 'close',
			parameters: { period: 20, stdev_multiplier: '2' }
		});
		expect(
			serializeIndicator(
				{
					id: 'hour_sma',
					kind: 'sma',
					input: 'close',
					timeframe: '1h',
					parameters: { period: 20 }
				},
				'5m'
			)
		).toEqual({
			id: 'hour_sma',
			kind: 'sma',
			input: 'close',
			parameters: { period: 20 },
			timeframe: '1h'
		});
		expect(
			serializeIndicator({
				id: 'ema_fast',
				kind: 'ema',
				input: 'close',
				timeframe: '5m',
				parameters: { period: 20 }
			}, '5m')
		).toEqual({
			id: 'ema_fast',
			kind: 'ema',
			input: 'close',
			parameters: { period: 20 }
		});
	});
});
