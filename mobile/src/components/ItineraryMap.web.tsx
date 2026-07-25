import { StyleSheet, Text, View } from 'react-native';
import { Image } from 'expo-image';
import { Ionicons } from '@expo/vector-icons';
import type { Itinerary } from '@/types';
import { colors, radius, shadow, type } from '@/theme';

const routeTexture = require('../../assets/generated/route-grid.png');

export function ItineraryMap({ itinerary }: { itinerary: Itinerary }) {
  const points = itinerary.days
    .flatMap((day) => day.items)
    .filter(
      (item): item is typeof item & { latitude: number; longitude: number } =>
        typeof item.latitude === 'number' && typeof item.longitude === 'number',
    );

  if (!points.length) return null;

  const latitudes = points.map((point) => point.latitude);
  const longitudes = points.map((point) => point.longitude);
  const minLatitude = Math.min(...latitudes);
  const maxLatitude = Math.max(...latitudes);
  const minLongitude = Math.min(...longitudes);
  const maxLongitude = Math.max(...longitudes);
  const latitudeRange = Math.max(maxLatitude - minLatitude, 0.001);
  const longitudeRange = Math.max(maxLongitude - minLongitude, 0.001);

  return (
    <View style={styles.frame}>
      <Image source={routeTexture} style={StyleSheet.absoluteFill} contentFit="cover" />
      <View style={styles.tint} />
      <View style={styles.mapHeader}>
        <View style={styles.mapHeading}>
          <View style={styles.mapIcon}>
            <Ionicons name="map-outline" size={15} color={colors.primary} />
          </View>
          <View>
            <Text style={styles.eyebrow}>Route overview</Text>
            <Text style={styles.title}>{points.length} mapped stops</Text>
          </View>
        </View>
        <View style={styles.livePill}>
          <View style={styles.liveDot} />
          <Text style={styles.liveText}>Live route</Text>
        </View>
      </View>
      <View style={styles.plot}>
        {points.slice(0, 8).map((point, index) => {
          const left = 8 + ((point.longitude - minLongitude) / longitudeRange) * 78;
          const top = 8 + ((maxLatitude - point.latitude) / latitudeRange) * 62;
          return (
            <View
              key={point.id}
              accessibilityLabel={`${index + 1}. ${point.title}`}
              style={[
                styles.marker,
                {
                  left: `${left}%`,
                  top: `${top}%`,
                },
              ]}
            >
              <Text style={styles.markerText}>{index + 1}</Text>
            </View>
          );
        })}
      </View>
      <View style={styles.mapFooter}>
        <Ionicons name="navigate-outline" size={13} color={colors.primary} />
        <Text style={styles.footerText} numberOfLines={1}>
          {points[0]?.title}
          {points.length > 1 ? ` → ${points.at(-1)?.title}` : ''}
        </Text>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  frame: {
    height: 150,
    overflow: 'hidden',
    borderRadius: radius.medium,
    backgroundColor: colors.surfaceTint,
    borderWidth: 1,
    borderColor: colors.line,
    ...shadow,
  },
  tint: {
    position: 'absolute',
    top: 0,
    right: 0,
    bottom: 0,
    left: 0,
    backgroundColor: colors.surfaceTint,
    opacity: 0.65,
  },
  mapHeader: {
    position: 'absolute',
    top: 9,
    left: 9,
    right: 9,
    minHeight: 44,
    borderRadius: 13,
    paddingHorizontal: 9,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    backgroundColor: colors.surface,
    ...shadow,
  },
  mapHeading: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
  },
  mapIcon: {
    width: 28,
    height: 28,
    borderRadius: 9,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: colors.primarySoft,
  },
  eyebrow: {
    ...type.caption,
    color: colors.faint,
    fontSize: 9,
    lineHeight: 12,
    textTransform: 'uppercase',
    letterSpacing: 0.55,
  },
  title: {
    ...type.label,
    color: colors.ink,
  },
  livePill: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
    paddingHorizontal: 7,
    paddingVertical: 5,
    borderRadius: radius.pill,
    backgroundColor: colors.greenSoft,
  },
  liveDot: {
    width: 5,
    height: 5,
    borderRadius: 3,
    backgroundColor: colors.green,
  },
  liveText: {
    ...type.caption,
    color: colors.green,
    fontSize: 9,
  },
  plot: {
    position: 'absolute',
    top: 54,
    right: 0,
    bottom: 32,
    left: 0,
  },
  marker: {
    position: 'absolute',
    width: 22,
    height: 22,
    marginLeft: -11,
    marginTop: -11,
    borderRadius: 11,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: colors.primary,
    borderWidth: 3,
    borderColor: colors.surface,
    ...shadow,
  },
  markerText: {
    ...type.caption,
    color: colors.surface,
    fontSize: 8,
    lineHeight: 10,
  },
  mapFooter: {
    position: 'absolute',
    left: 9,
    right: 9,
    bottom: 7,
    height: 27,
    borderRadius: 10,
    paddingHorizontal: 8,
    flexDirection: 'row',
    gap: 6,
    alignItems: 'center',
    backgroundColor: colors.surface,
  },
  footerText: {
    ...type.caption,
    color: colors.ink,
    flex: 1,
    fontSize: 9,
  },
});
