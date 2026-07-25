import { Ionicons } from '@expo/vector-icons';
import { StyleSheet, View } from 'react-native';
import { colors } from '@/theme';

export function BrandMark({ size = 48 }: { size?: number }) {
  const planeSize = Math.round(size * 0.47);

  return (
    <View
      accessible={false}
      importantForAccessibility="no-hide-descendants"
      style={[styles.mark, { width: size, height: size, borderRadius: size * 0.32 }]}
    >
      <View
        style={[
          styles.halo,
          {
            width: size * 0.68,
            height: size * 0.68,
            borderRadius: size * 0.34,
          },
        ]}
      />
      <Ionicons
        color={colors.surface}
        name="airplane"
        size={planeSize}
        style={styles.plane}
      />
      <View
        style={[
          styles.destination,
          {
            width: Math.max(3, size * 0.1),
            height: Math.max(3, size * 0.1),
            borderRadius: size,
          },
        ]}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  mark: {
    backgroundColor: colors.primary,
    justifyContent: 'center',
    alignItems: 'center',
    overflow: 'hidden',
  },
  halo: {
    position: 'absolute',
    backgroundColor: colors.primaryLight,
    opacity: 0.78,
  },
  plane: {
    transform: [{ rotate: '-18deg' }],
  },
  destination: {
    position: 'absolute',
    right: '15%',
    top: '15%',
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.primarySoft,
  },
});
