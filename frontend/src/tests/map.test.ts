/**
 * The map module.
 *
 * The geometry itself is checked on the Python side (`tests/test_india_map.py`), against the script
 * that generates it. What is tested here is the arithmetic the components lean on: the scale that
 * turns a figure into a shade, the crop that frames a case, and the lookups that decide whether a
 * district gets drawn at all. Each of those fails quietly if it is wrong — a wrong shade still
 * paints, a wrong crop still renders a map — so none of them can be left to the eye.
 */
import { describe, expect, it } from 'vitest';
import {
	anchorPlacement,
	crop,
	districtByName,
	findDistrict,
	india,
	intersects,
	makeScale,
	numeric,
	stateByName,
	text,
	viewBox,
	WHOLE_COUNTRY,
	type MapShape
} from '$lib/map';

const shape = (bbox: [number, number, number, number], name = 'X'): MapShape => ({
	name,
	d: 'M0 0Z',
	cx: (bbox[0] + bbox[2]) / 2,
	cy: (bbox[1] + bbox[3]) / 2,
	bbox
});

describe('makeScale', () => {
	it('puts the largest value in the top step and the smallest in the bottom', () => {
		const scale = makeScale([36, 42, 43, 55, 63, 69, 72, 111, 116, 175]);
		expect(scale.bin(175)).toBe(scale.steps - 1);
		expect(scale.bin(36)).toBe(0);
		expect(scale.min).toBe(36);
		expect(scale.max).toBe(175);
	});

	it('never returns a step outside the ramp, whatever it is handed', () => {
		const scale = makeScale([10, 20, 30]);
		for (const value of [-500, 0, 10, 20, 30, 500, Number.NaN, Infinity]) {
			const step = scale.bin(value);
			expect(step).toBeGreaterThanOrEqual(0);
			expect(step).toBeLessThan(scale.steps);
		}
	});

	it('survives every value being identical, which equal intervals would divide by zero on', () => {
		const scale = makeScale([7, 7, 7]);
		expect(scale.bin(7)).toBe(scale.steps - 1);
		expect(() => scale.range(0)).not.toThrow();
	});

	it('survives an empty table rather than reporting -Infinity as a bound', () => {
		const scale = makeScale([]);
		expect(Number.isFinite(scale.min)).toBe(true);
		expect(Number.isFinite(scale.max)).toBe(true);
	});

	it('reports steps that tile the range without a gap or an overlap', () => {
		const scale = makeScale([0, 100], 5);
		const ranges = Array.from({ length: scale.steps }, (_, i) => scale.range(i));
		expect(ranges[0]?.[0]).toBe(0);
		expect(ranges[scale.steps - 1]?.[1]).toBe(100);
		for (let i = 1; i < ranges.length; i++) {
			expect(ranges[i]?.[0]).toBeCloseTo(ranges[i - 1]?.[1] ?? -1, 9);
		}
	});

	it('bins every value into the step whose range contains it', () => {
		const values = [36, 42, 55, 72, 111, 175];
		const scale = makeScale(values);
		for (const value of values) {
			const [low, high] = scale.range(scale.bin(value));
			expect(value).toBeGreaterThanOrEqual(low - 1e-9);
			expect(value).toBeLessThanOrEqual(high + 1e-9);
		}
	});
});

describe('crop', () => {
	it('frames the shapes it is given', () => {
		const box = crop([shape([400, 300, 450, 360])]);
		expect(box.x).toBeLessThan(400);
		expect(box.y).toBeLessThan(300);
		expect(box.x + box.width).toBeGreaterThan(450);
		expect(box.y + box.height).toBeGreaterThan(360);
	});

	it('keeps context around two adjacent districts instead of filling the frame with them', () => {
		// Bahraich and Gonda are about 50 units apart; cropped tight the reader loses the country.
		const box = crop([shape([402, 304, 425, 351], 'a'), shape([417, 338, 451, 361], 'b')]);
		expect(box.width).toBeGreaterThanOrEqual(150);
		expect(box.height).toBeGreaterThanOrEqual(150);
	});

	it('centres the grown frame on the shapes rather than pushing them to a corner', () => {
		const box = crop([shape([400, 300, 420, 320])]);
		expect(box.x + box.width / 2).toBeCloseTo(410, 6);
		expect(box.y + box.height / 2).toBeCloseTo(310, 6);
	});

	it('falls back to the whole country when there is nothing to frame', () => {
		expect(crop([])).toEqual(WHOLE_COUNTRY);
	});
});

describe('intersects', () => {
	const box = { x: 100, y: 100, width: 100, height: 100 };

	it('accepts a shape overlapping the frame and one merely touching its edge', () => {
		expect(intersects(shape([150, 150, 250, 250]), box)).toBe(true);
		expect(intersects(shape([0, 0, 100, 100]), box)).toBe(true);
	});

	it('rejects a shape entirely outside it, which is what keeps a case page small', () => {
		expect(intersects(shape([0, 0, 99, 99]), box)).toBe(false);
		expect(intersects(shape([201, 201, 300, 300]), box)).toBe(false);
	});
});

describe('lookups', () => {
	it('finds a state the evaluation names', () => {
		expect(stateByName('Uttar Pradesh')?.name).toBe('Uttar Pradesh');
	});

	it('returns undefined rather than throwing on a name the map does not carry', () => {
		expect(stateByName('Atlantis')).toBeUndefined();
		expect(districtByName('Atlantis', 'Nowhere')).toBeUndefined();
		expect(findDistrict('Atlantis')).toBeUndefined();
	});

	it('resolves a district by name alone, which is all the nearest alternative gives it', () => {
		const gonda = findDistrict('Gonda');
		expect(gonda?.name).toBe('Gonda');
		expect(gonda?.state).toBe('Uttar Pradesh');
	});

	it('resolves an alternative that lies in another state', () => {
		// Barwani is in Madhya Pradesh; the nearest provider is across the Maharashtra border.
		expect(districtByName('Barwani', 'Madhya Pradesh')?.state).toBe('Madhya Pradesh');
		expect(findDistrict('Nandurbar')?.state).toBe('Maharashtra');
	});

	it('will not match a district against the wrong state', () => {
		expect(districtByName('Gonda', 'Madhya Pradesh')).toBeUndefined();
	});
});

describe('anchorPlacement', () => {
	it('turns a tooltip inward near the right edge so it stays in frame', () => {
		expect(anchorPlacement(india.width * 0.9, india.height * 0.5).side).toBe('left');
		expect(anchorPlacement(india.width * 0.1, india.height * 0.5).side).toBe('right');
	});

	it('drops a tooltip below its anchor near the top edge', () => {
		expect(anchorPlacement(india.width * 0.5, 0).vertical).toBe('below');
		expect(anchorPlacement(india.width * 0.5, india.height * 0.9).vertical).toBe('above');
	});

	it('measures against the frame it is given, not always the whole country', () => {
		const box = { x: 400, y: 300, width: 100, height: 100 };
		// Far right of the country, but on the left of this frame.
		expect(anchorPlacement(420, 380, box).side).toBe('right');
		expect(anchorPlacement(490, 380, box).side).toBe('left');
	});
});

describe('viewBox', () => {
	it('writes the four numbers SVG expects, rounded', () => {
		expect(viewBox({ x: 1.234, y: 2.345, width: 10.567, height: 20.891 })).toBe(
			'1.2 2.3 10.6 20.9'
		);
	});
});

describe('row readers', () => {
	it('reads a number and refuses a string, so a label cannot become a shade', () => {
		expect(numeric({ km: 50, state: 'Bihar' }, 'km')).toBe(50);
		expect(numeric({ km: 50, state: 'Bihar' }, 'state')).toBeNaN();
		expect(numeric({}, 'missing')).toBeNaN();
	});

	it('reads a string and refuses a number', () => {
		expect(text({ km: 50, state: 'Bihar' }, 'state')).toBe('Bihar');
		expect(text({ km: 50, state: 'Bihar' }, 'km')).toBe('');
	});
});
