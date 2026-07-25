import { StyleSheet, View } from 'react-native';
import MapView, { Marker, Polyline } from 'react-native-maps';
import type { Itinerary } from '@/types';
import { colors, radius } from '@/theme';

export function ItineraryMap({ itinerary }: { itinerary: Itinerary }) {
  const points = itinerary.days
    .flatMap((day) => day.items)
    .filter(
      (item): item is typeof item & { latitude: number; longitude: number } =>
        typeof item.latitude === 'number' && typeof item.longitude === 'number',
    );
  if (!points.length) return null;
  const first = points[0];
  if (!first) return null;
  return (
    <View style={styles.frame}>
      <MapView
        style={StyleSheet.absoluteFill}
        initialRegion={{
          latitude: first.latitude,
          longitude: first.longitude,
          latitudeDelta: 0.18,
          longitudeDelta: 0.18,
        }}
        scrollEnabled={false}
        zoomEnabled={false}
        pitchEnabled={false}
        rotateEnabled={false}
      >
        {points.map((point, index) => (
          <Marker
            key={point.id}
            coordinate={{ latitude: point.latitude, longitude: point.longitude }}
            title={`${index + 1}. ${point.title}`}
            pinColor={index === 0 ? colors.coral : colors.blue}
          />
        ))}
        <Polyline
          coordinates={points.map((point) => ({
            latitude: point.latitude,
            longitude: point.longitude,
          }))}
          strokeColor={colors.blue}
          strokeWidth={3}
        />
      </MapView>
    </View>
  );
}

const styles = StyleSheet.create({
  frame: {
    height: 170,
    overflow: 'hidden',
    borderRadius: radius.medium,
    backgroundColor: colors.blueSoft,
  },
});

