import type { ImageSource } from 'expo-image';

export const tripImagePool: readonly ImageSource[] = [
  require('../../assets/generated/bali-sunset.png'),
  require('../../assets/generated/manali-valley.png'),
  require('../../assets/generated/trip-jaipur.png'),
  require('../../assets/generated/trip-kerala.png'),
  require('../../assets/generated/trip-ladakh.png'),
  require('../../assets/generated/trip-meghalaya.png'),
  require('../../assets/generated/trip-kyoto.png'),
  require('../../assets/generated/trip-paris.png'),
  require('../../assets/generated/trip-santorini.png'),
  require('../../assets/generated/trip-switzerland.png'),
];

function hashKey(key: string): number {
  let hash = 2166136261;

  for (let index = 0; index < key.length; index += 1) {
    hash ^= key.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }

  return hash >>> 0;
}

export function tripImageForKey(key: string): ImageSource {
  return tripImagePool[hashKey(key) % tripImagePool.length] as ImageSource;
}

export function tripImageAssignments(
  keys: readonly string[],
): ReadonlyMap<string, ImageSource> {
  const uniqueKeys = [...new Set(keys.filter(Boolean))].sort();
  const usage = tripImagePool.map(() => 0);
  const assignments = new Map<string, ImageSource>();

  uniqueKeys.forEach((key) => {
    const rankedIndexes = tripImagePool
      .map((_, index) => index)
      .sort((left, right) => {
        const usageDifference = (usage[left] ?? 0) - (usage[right] ?? 0);
        if (usageDifference) return usageDifference;
        return hashKey(`${key}:${right}`) - hashKey(`${key}:${left}`);
      });
    const selectedIndex = rankedIndexes[0] ?? 0;
    usage[selectedIndex] = (usage[selectedIndex] ?? 0) + 1;
    assignments.set(key, tripImagePool[selectedIndex] as ImageSource);
  });

  return assignments;
}
