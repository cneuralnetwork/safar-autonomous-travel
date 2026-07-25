import assert from 'node:assert/strict';
import test from 'node:test';
import type { Itinerary } from '../types.ts';
import { buildItineraryIcs, itineraryIcsFilename } from './ics.ts';

const itinerary: Itinerary = {
  timezone: 'Asia/Kolkata',
  days: [
    {
      date: '2026-08-01',
      title: 'Arrival',
      items: [
        {
          id: 'flight-1',
          title: 'Fly to Goa, then relax',
          description: 'Window seat; carry ID',
          start_at: '2026-08-01T09:30:00+05:30',
          end_at: '2026-08-01T12:00:00+05:30',
          location: 'Kolkata, CCU',
          category: 'flight',
        },
        {
          id: 'buffer-1',
          title: 'Airport buffer',
          description: '',
          start_at: '2026-08-01T08:00:00+05:30',
          end_at: '2026-08-01T09:00:00+05:30',
          category: 'buffer',
        },
      ],
    },
  ],
};

test('buildItineraryIcs creates a portable calendar without buffer items', () => {
  const output = buildItineraryIcs(
    itinerary,
    'Goa, friends & fun',
    new Date('2026-07-25T10:00:00Z'),
  );

  assert.match(output, /BEGIN:VCALENDAR\r\nVERSION:2.0/);
  assert.match(output, /SUMMARY:Fly to Goa\\, then relax/);
  assert.match(output, /DESCRIPTION:Window seat\\; carry ID/);
  assert.match(output, /DTSTART:20260801T040000Z/);
  assert.doesNotMatch(output, /Airport buffer/);
  assert.match(output, /END:VCALENDAR\r\n$/);
});

test('itineraryIcsFilename produces a safe calendar filename', () => {
  assert.equal(itineraryIcsFilename('Goa, friends & fun'), 'goa-friends-fun.ics');
});
