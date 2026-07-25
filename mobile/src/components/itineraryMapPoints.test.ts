import assert from 'node:assert/strict';
import test from 'node:test';
import { itineraryMapPoints } from './itineraryMapPoints.ts';
import type { Itinerary } from '../types.ts';

test('keeps real itinerary coordinates and never invents missing map points', () => {
  const itinerary: Itinerary = {
    timezone: 'Asia/Kolkata',
    days: [
      {
        date: '2026-08-01',
        title: 'Explore Goa',
        items: [
          {
            id: 'real-place',
            title: 'Reis Magos Fort',
            description: 'Visit the fort',
            start_at: '2026-08-01T10:00:00+05:30',
            end_at: '2026-08-01T12:00:00+05:30',
            location: 'Reis Magos, Goa',
            latitude: 15.5007,
            longitude: 73.9116,
            category: 'activity',
          },
          {
            id: 'unresolved-place',
            title: 'Flexible lunch',
            description: 'Choose nearby',
            start_at: '2026-08-01T13:00:00+05:30',
            end_at: '2026-08-01T14:00:00+05:30',
            category: 'meal',
          },
        ],
      },
    ],
  };

  assert.deepEqual(itineraryMapPoints(itinerary), [
    {
      id: 'real-place',
      title: 'Reis Magos Fort',
      location: 'Reis Magos, Goa',
      category: 'activity',
      startAt: '2026-08-01T10:00:00+05:30',
      latitude: 15.5007,
      longitude: 73.9116,
    },
  ]);
});
