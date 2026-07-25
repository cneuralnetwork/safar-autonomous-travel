import { Pressable, StyleSheet, Text, View } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import * as Haptics from 'expo-haptics';
import { colors, radius, shadow, type } from '@/theme';

export type TabKey = 'plan' | 'trips' | 'activity' | 'profile';

const items: Array<{
  key: TabKey;
  label: string;
  icon: keyof typeof Ionicons.glyphMap;
  activeIcon: keyof typeof Ionicons.glyphMap;
}> = [
  { key: 'plan', label: 'Home', icon: 'home-outline', activeIcon: 'home' },
  { key: 'trips', label: 'Trips', icon: 'briefcase-outline', activeIcon: 'briefcase' },
  {
    key: 'activity',
    label: 'Chat',
    icon: 'chatbubble-ellipses-outline',
    activeIcon: 'chatbubble-ellipses',
  },
  { key: 'profile', label: 'Account', icon: 'person-outline', activeIcon: 'person' },
];

interface BottomDockProps {
  active: TabKey;
  onChange: (tab: TabKey) => void;
  onCreate?: () => void;
}

export function BottomDock({
  active,
  onChange,
  onCreate,
}: BottomDockProps) {
  const renderItem = (item: (typeof items)[number]) => {
    const selected = item.key === active;

    return (
      <Pressable
        key={item.key}
        style={({ pressed }) => [styles.item, pressed && styles.pressed]}
        onPress={() => {
          void Haptics.selectionAsync();
          onChange(item.key);
        }}
        accessibilityRole="tab"
        accessibilityState={{ selected }}
        accessibilityLabel={item.label}
      >
        <View style={[styles.iconWell, selected && styles.iconWellActive]}>
          <Ionicons
            name={selected ? item.activeIcon : item.icon}
            size={20}
            color={selected ? colors.surface : colors.whiteMuted}
          />
        </View>
        <Text style={[styles.label, selected && styles.labelActive]}>{item.label}</Text>
      </Pressable>
    );
  };

  return (
    <View style={styles.dock}>
      {items.slice(0, 2).map(renderItem)}
      <View style={styles.createSlot}>
        <Pressable
          style={({ pressed }) => [styles.createButton, pressed && styles.createPressed]}
          onPress={() => {
            void Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium);
            if (onCreate) {
              onCreate();
              return;
            }
            onChange('plan');
          }}
          accessibilityRole="button"
          accessibilityLabel="Start a new trip"
          accessibilityHint="Opens a fresh trip conversation"
        >
          <Ionicons name="add" size={32} color={colors.surface} />
        </Pressable>
      </View>
      {items.slice(2).map(renderItem)}
    </View>
  );
}

const styles = StyleSheet.create({
  dock: {
    height: 78,
    paddingHorizontal: 7,
    paddingTop: 7,
    paddingBottom: 6,
    borderRadius: 25,
    backgroundColor: colors.dock,
    flexDirection: 'row',
    alignItems: 'center',
    ...shadow,
  },
  item: {
    flex: 1,
    height: 64,
    alignItems: 'center',
    justifyContent: 'center',
    gap: 2,
  },
  iconWell: {
    width: 34,
    height: 32,
    borderRadius: 11,
    alignItems: 'center',
    justifyContent: 'center',
  },
  iconWellActive: {
    backgroundColor: colors.dockActive,
  },
  label: {
    ...type.caption,
    color: colors.whiteMuted,
    fontSize: 9,
    lineHeight: 13,
  },
  labelActive: {
    color: colors.surface,
  },
  createSlot: {
    flex: 1,
    height: 64,
    alignItems: 'center',
    justifyContent: 'flex-start',
  },
  createButton: {
    width: 62,
    height: 62,
    marginTop: -17,
    borderRadius: radius.pill,
    borderWidth: 4,
    borderColor: colors.surface,
    backgroundColor: colors.primary,
    alignItems: 'center',
    justifyContent: 'center',
    ...shadow,
  },
  pressed: { opacity: 0.72, transform: [{ scale: 0.97 }] },
  createPressed: { opacity: 0.88, transform: [{ scale: 0.94 }] },
});
