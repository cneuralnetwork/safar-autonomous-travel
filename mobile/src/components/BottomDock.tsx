import { Pressable, StyleSheet, Text, View } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import * as Haptics from 'expo-haptics';
import { colors, shadow, type } from '@/theme';

export type TabKey = 'plan' | 'trips' | 'activity' | 'profile';

const items: Array<{
  key: TabKey;
  label: string;
  icon: keyof typeof Ionicons.glyphMap;
  activeIcon: keyof typeof Ionicons.glyphMap;
}> = [
  { key: 'plan', label: 'Plan', icon: 'sparkles-outline', activeIcon: 'sparkles' },
  { key: 'trips', label: 'Trips', icon: 'wallet-outline', activeIcon: 'wallet' },
  { key: 'activity', label: 'Activity', icon: 'pulse-outline', activeIcon: 'pulse' },
  { key: 'profile', label: 'You', icon: 'person-outline', activeIcon: 'person' },
];

export function BottomDock({
  active,
  onChange,
}: {
  active: TabKey;
  onChange: (tab: TabKey) => void;
}) {
  return (
    <View style={styles.dock}>
      {items.map((item) => {
        const selected = item.key === active;
        return (
          <Pressable
            key={item.key}
            style={({ pressed }) => [
              styles.item,
              selected && styles.active,
              pressed && styles.pressed,
            ]}
            onPress={() => {
              void Haptics.selectionAsync();
              onChange(item.key);
            }}
            accessibilityRole="tab"
            accessibilityState={{ selected }}
            accessibilityLabel={item.label}
          >
            <Ionicons
              name={selected ? item.activeIcon : item.icon}
              size={20}
              color={selected ? colors.surface : '#8D8F91'}
            />
            {selected ? <Text style={styles.label}>{item.label}</Text> : null}
          </Pressable>
        );
      })}
    </View>
  );
}

const styles = StyleSheet.create({
  dock: {
    height: 68,
    padding: 8,
    borderRadius: 34,
    backgroundColor: colors.dock,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    ...shadow,
  },
  item: {
    height: 52,
    minWidth: 52,
    paddingHorizontal: 15,
    borderRadius: 26,
    alignItems: 'center',
    justifyContent: 'center',
    flexDirection: 'row',
    gap: 8,
  },
  active: { backgroundColor: colors.dockActive, minWidth: 106 },
  label: { ...type.label, color: colors.surface },
  pressed: { opacity: 0.72, transform: [{ scale: 0.97 }] },
});

