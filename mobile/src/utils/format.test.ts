import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import { formatCurrency, formatDate, formatTime } from './format.ts';

describe('travel formatting', () => {
  it('formats Indian rupee amounts without decimals', () => {
    assert.match(formatCurrency(18750), /₹\s?18,750/);
  });

  it('formats itinerary dates in India locale', () => {
    assert.match(formatDate('2026-08-14'), /14 Aug/);
  });

  it('formats ISO timestamps as readable times', () => {
    assert.match(formatTime('2026-08-14T09:30:00+05:30'), /9:30/);
  });
});
