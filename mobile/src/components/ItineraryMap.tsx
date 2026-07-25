import { StyleSheet, Text, View } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import type { Itinerary } from '@/types';
import { colors, radius, shadow, type } from '@/theme';
import LeafletRouteMap from '@/components/LeafletRouteMap';
import { itineraryMapPoints } from '@/components/itineraryMapPoints';

export function ItineraryMap({
  itinerary,
  expanded = false,
}: {
  itinerary: Itinerary;
  expanded?: boolean;
}) {
  const points = itineraryMapPoints(itinerary);
  if (!points.length) return null;

  return (
    <View style={[styles.frame, expanded && styles.frameExpanded]}>
      <View style={styles.mapHeader}>
        <View style={styles.mapHeading}>
          <View style={styles.mapIcon}>
            <Ionicons name="map-outline" size={15} color={colors.primary} />
          </View>
          <View>
            <Text style={styles.eyebrow}>Real route</Text>
            <Text style={styles.title}>{points.length} mapped stops</Text>
          </View>
        </View>
        <View style={styles.osmPill}>
          <View style={styles.osmDot} />
          <Text style={styles.osmText}>OpenStreetMap</Text>
        </View>
      </View>
      <View style={styles.mapSurface}>
        <LeafletRouteMap
          points={points}
          expanded={expanded}
          dom={{
            scrollEnabled: false,
            style: styles.domView,
            containerStyle: styles.domView,
          }}
        />
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
    height: 224,
    overflow: 'hidden',
    borderRadius: radius.medium,
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.line,
    ...shadow,
  },
  frameExpanded: {
    height: 470,
  },
  mapHeader: {
    minHeight: 55,
    paddingHorizontal: 11,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    backgroundColor: colors.surface,
  },
  mapHeading: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
  },
  mapIcon: {
    width: 30,
    height: 30,
    borderRadius: 10,
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
  osmPill: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 5,
    paddingHorizontal: 8,
    paddingVertical: 5,
    borderRadius: radius.pill,
    backgroundColor: colors.greenSoft,
  },
  osmDot: {
    width: 5,
    height: 5,
    borderRadius: 3,
    backgroundColor: colors.green,
  },
  osmText: {
    ...type.caption,
    color: colors.green,
    fontSize: 8,
  },
  mapSurface: {
    flex: 1,
    overflow: 'hidden',
    borderTopWidth: 1,
    borderBottomWidth: 1,
    borderColor: colors.line,
    backgroundColor: colors.surfaceTint,
  },
  domView: {
    flex: 1,
    width: '100%',
    height: '100%',
    backgroundColor: colors.surfaceTint,
  },
  mapFooter: {
    height: 31,
    paddingHorizontal: 11,
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
