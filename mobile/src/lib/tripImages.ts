import type { ImageSource } from 'expo-image';
import type { VisualTheme } from '@/types';

const images = {
  bali: require('../../assets/generated/bali-sunset.png') as ImageSource,
  beach: require('../../assets/generated/goa-postcard.png') as ImageSource,
  manali: require('../../assets/generated/manali-valley.png') as ImageSource,
  jaipur: require('../../assets/generated/trip-jaipur.png') as ImageSource,
  kerala: require('../../assets/generated/trip-kerala.png') as ImageSource,
  ladakh: require('../../assets/generated/trip-ladakh.png') as ImageSource,
  meghalaya: require('../../assets/generated/trip-meghalaya.png') as ImageSource,
  kyoto: require('../../assets/generated/trip-kyoto.png') as ImageSource,
  paris: require('../../assets/generated/trip-paris.png') as ImageSource,
  santorini: require('../../assets/generated/trip-santorini.png') as ImageSource,
  switzerland: require('../../assets/generated/trip-switzerland.png') as ImageSource,
} as const;

const themePools: Record<VisualTheme, readonly ImageSource[]> = {
  coast: [images.beach, images.santorini, images.bali],
  mountains: [images.manali, images.ladakh, images.switzerland],
  heritage: [images.jaipur, images.kyoto],
  nature: [images.kerala, images.meghalaya, images.bali],
  city: [images.paris, images.kyoto],
};

const destinationImages: ReadonlyArray<{
  destinations: readonly string[];
  image: ImageSource;
}> = [
  { destinations: ['bali'], image: images.bali },
  {
    destinations: [
      'chennai',
      'goa',
      'gokarna',
      'mahabalipuram',
      'pondicherry',
      'puducherry',
      'varkala',
    ],
    image: images.beach,
  },
  { destinations: ['manali'], image: images.manali },
  { destinations: ['jaipur'], image: images.jaipur },
  {
    destinations: ['kerala', 'kochi', 'cochin', 'alleppey', 'kumarakom', 'munnar'],
    image: images.kerala,
  },
  { destinations: ['ladakh', 'leh'], image: images.ladakh },
  {
    destinations: ['meghalaya', 'cherrapunji', 'shillong'],
    image: images.meghalaya,
  },
  { destinations: ['kyoto'], image: images.kyoto },
  { destinations: ['paris'], image: images.paris },
  { destinations: ['santorini'], image: images.santorini },
  { destinations: ['switzerland'], image: images.switzerland },
];

const destinationThemes: Record<VisualTheme, readonly string[]> = {
  coast: [
    'alibaug',
    'andaman',
    'bali',
    'chennai',
    'goa',
    'gokarna',
    'kovalam',
    'lakshadweep',
    'mahabalipuram',
    'maldives',
    'mangalore',
    'mumbai',
    'pondicherry',
    'puducherry',
    'santorini',
    'varkala',
    'visakhapatnam',
  ],
  mountains: [
    'auli',
    'darjeeling',
    'dharamshala',
    'gangtok',
    'gulmarg',
    'kashmir',
    'ladakh',
    'leh',
    'manali',
    'mussoorie',
    'nainital',
    'shimla',
    'srinagar',
    'switzerland',
  ],
  heritage: [
    'agra',
    'amritsar',
    'hampi',
    'jaipur',
    'jaisalmer',
    'jodhpur',
    'khajuraho',
    'kyoto',
    'lucknow',
    'mysore',
    'udaipur',
    'varanasi',
  ],
  nature: [
    'alleppey',
    'assam',
    'cherrapunji',
    'coorg',
    'kerala',
    'kochi',
    'kumarakom',
    'meghalaya',
    'munnar',
    'ooty',
    'thekkady',
    'wayanad',
  ],
  city: [
    'ahmedabad',
    'bangalore',
    'bengaluru',
    'delhi',
    'dubai',
    'hyderabad',
    'kolkata',
    'london',
    'paris',
    'pune',
    'singapore',
    'tokyo',
  ],
};

export interface TripImageContext {
  key: string;
  destination?: string;
  visualTheme?: VisualTheme;
}

function hashKey(key: string): number {
  let hash = 2166136261;

  for (let index = 0; index < key.length; index += 1) {
    hash ^= key.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }

  return hash >>> 0;
}

function normalizedDestination(destination?: string): string {
  return (destination || '').trim().toLocaleLowerCase();
}

export function visualThemeForDestination(destination?: string): VisualTheme {
  const normalized = normalizedDestination(destination);
  if (!normalized) return 'city';

  for (const [theme, candidates] of Object.entries(destinationThemes) as Array<
    [VisualTheme, readonly string[]]
  >) {
    if (
      candidates.some(
        (candidate) =>
          normalized === candidate ||
          normalized.includes(candidate) ||
          candidate.includes(normalized),
      )
    ) {
      return theme;
    }
  }

  return 'city';
}

export function tripImageForTrip({
  key,
  destination,
  visualTheme,
}: TripImageContext): ImageSource {
  const normalized = normalizedDestination(destination);
  const exactMatch = destinationImages.find(({ destinations }) =>
    destinations.some(
      (candidate) =>
        normalized === candidate || normalized.includes(candidate),
    ),
  );
  if (exactMatch) return exactMatch.image;

  const theme = visualTheme || visualThemeForDestination(destination);
  const pool = themePools[theme];
  return pool[hashKey(`${normalized}:${key}`) % pool.length] as ImageSource;
}

export function tripImageAssignments(
  trips: readonly TripImageContext[],
): ReadonlyMap<string, ImageSource> {
  const assignments = new Map<string, ImageSource>();

  trips.forEach((trip) => {
    if (!trip.key || assignments.has(trip.key)) return;
    assignments.set(trip.key, tripImageForTrip(trip));
  });

  return assignments;
}
