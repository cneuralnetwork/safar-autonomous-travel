import { StyleSheet, Text, View } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import MapView, { Marker, Polyline } from 'react-native-maps';
import type { Itinerary } from '@/types';
import { colors, radius, shadow, type } from '@/theme';

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
  const latitudeDelta = Math.max((maxLatitude - minLatitude) * 1.7, 0.08);
  const longitudeDelta = Math.max((maxLongitude - minLongitude) * 1.7, 0.08);

  return (
    <View style={styles.frame}>
      <MapView
        style={StyleSheet.absoluteFill}
        initialRegion={{
          latitude: (minLatitude + maxLatitude) / 2,
          longitude: (minLongitude + maxLongitude) / 2,
          latitudeDelta,
          longitudeDelta,
        }}
        scrollEnabled={false}
        zoomEnabled={false}
        pitchEnabled={false}
        rotateEnabled={false}
        toolbarEnabled={false}
      >
        {points.map((point, index) => (
          <Marker
            key={point.id}
            coordinate={{ latitude: point.latitude, longitude: point.longitude }}
            title={`${index + 1}. ${point.title}`}
          >
            <View style={styles.marker}>
              <Text style={styles.markerText}>{index + 1}</Text>
            </View>
          </Marker>
        ))}
        {points.length > 1 ? (
          <Polyline
            coordinates={points.map((point) => ({
              latitude: point.latitude,
              longitude: point.longitude,
            }))}
            strokeColor={colors.primary}
            strokeWidth={3}
          />
        ) : null}
      </MapView>
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
    height: 164,
    overflow: 'hidden',
    borderRadius: radius.medium,
    backgroundColor: colors.surfaceTint,
    borderWidth: 1,
    borderColor: colors.line,
  },
  marker: {
    width: 25,
    height: 25,
    borderRadius: 13,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: colors.primary,
    borderWidth: 3,
    borderColor: colors.surface,
  },
  markerText: {
    ...type.caption,
    color: colors.surface,
    fontSize: 8,
    lineHeight: 10,
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
    ...shadow,
  },
  footerText: {
    ...type.caption,
    color: colors.ink,
    flex: 1,
    fontSize: 9,
  },
});
