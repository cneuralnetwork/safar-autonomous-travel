import { StyleSheet, Text, View } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import type { Itinerary } from '@/types';
import { colors, radius, type } from '@/theme';

export function ItineraryMap({ itinerary }: { itinerary: Itinerary }) {
  const count = itinerary.days.flatMap((day) => day.items).filter((item) => item.latitude).length;
  if (!count) return null;
  return (
    <View style={styles.frame}>
      <Ionicons name="map" size={26} color={colors.blue} />
      <Text style={styles.text}>{count} itinerary stops mapped in the mobile app</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  frame: {
    height: 110,
    borderRadius: radius.medium,
    backgroundColor: colors.blueSoft,
    alignItems: 'center',
    justifyContent: 'center',
    gap: 8,
  },
  text: { ...type.label, color: colors.ink },
});

