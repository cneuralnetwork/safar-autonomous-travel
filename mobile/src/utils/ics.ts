import type { Itinerary } from '@/types';

function escapeIcsText(value: string) {
  return value
    .replaceAll('\\', '\\\\')
    .replaceAll('\n', '\\n')
    .replaceAll(',', '\\,')
    .replaceAll(';', '\\;');
}

function toUtcStamp(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    throw new Error(`Invalid itinerary date: ${value}`);
  }
  return date
    .toISOString()
    .replaceAll('-', '')
    .replaceAll(':', '')
    .replace(/\.\d{3}Z$/, 'Z');
}

function safeFilename(value: string) {
  const base = value
    .normalize('NFKD')
    .replace(/[^\w\s-]/g, '')
    .trim()
    .replace(/[\s_-]+/g, '-')
    .toLowerCase();
  return `${base || 'safar-trip'}.ics`;
}

export function buildItineraryIcs(
  itinerary: Itinerary,
  tripTitle: string,
  generatedAt = new Date(),
) {
  const items = itinerary.days.flatMap((day) =>
    day.items.filter((item) => item.category !== 'buffer'),
  );
  if (!items.length) {
    throw new Error('This itinerary does not have any calendar items yet.');
  }

  const stamp = toUtcStamp(generatedAt.toISOString());
  const lines = [
    'BEGIN:VCALENDAR',
    'VERSION:2.0',
    'PRODID:-//Safar//Travel Itinerary//EN',
    'CALSCALE:GREGORIAN',
    'METHOD:PUBLISH',
    `X-WR-CALNAME:${escapeIcsText(tripTitle)}`,
  ];

  for (const item of items) {
    lines.push(
      'BEGIN:VEVENT',
      `UID:${escapeIcsText(item.id)}@safar.travel`,
      `DTSTAMP:${stamp}`,
      `DTSTART:${toUtcStamp(item.start_at)}`,
      `DTEND:${toUtcStamp(item.end_at)}`,
      `SUMMARY:${escapeIcsText(item.title)}`,
    );
    if (item.description) {
      lines.push(`DESCRIPTION:${escapeIcsText(item.description)}`);
    }
    if (item.location) {
      lines.push(`LOCATION:${escapeIcsText(item.location)}`);
    }
    lines.push('END:VEVENT');
  }

  lines.push('END:VCALENDAR');
  return `${lines.join('\r\n')}\r\n`;
}

export function itineraryIcsFilename(tripTitle: string) {
  return safeFilename(tripTitle);
}
