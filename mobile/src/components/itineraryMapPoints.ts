import type { Itinerary } from '@/types';

export interface ItineraryMapPoint {
  id: string;
  title: string;
  location?: string;
  category: string;
  startAt: string;
  latitude: number;
  longitude: number;
}

export function itineraryMapPoints(
  itinerary: Itinerary,
): ItineraryMapPoint[] {
  return itinerary.days
    .flatMap((day) => day.items)
    .filter(
      (
        item,
      ): item is typeof item & {
        latitude: number;
        longitude: number;
      } =>
        typeof item.latitude === 'number' &&
        Number.isFinite(item.latitude) &&
        typeof item.longitude === 'number' &&
        Number.isFinite(item.longitude),
    )
    .map((item) => ({
      id: item.id,
      title: item.title,
      location: item.location,
      category: item.category,
      startAt: item.start_at,
      latitude: item.latitude,
      longitude: item.longitude,
    }));
}
