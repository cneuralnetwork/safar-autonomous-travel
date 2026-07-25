import { StyleSheet, View } from 'react-native';
import { colors } from '@/theme';

export function BrandMark({ size = 48 }: { size?: number }) {
  return (
    <View style={[styles.mark, { width: size, height: size, borderRadius: size * 0.32 }]}>
      <View style={[styles.route, { width: size * 0.5 }]} />
      <View style={[styles.dot, styles.first, { width: size * 0.12, height: size * 0.12 }]} />
      <View style={[styles.dot, styles.second, { width: size * 0.12, height: size * 0.12 }]} />
    </View>
  );
}

const styles = StyleSheet.create({
  mark: {
    backgroundColor: colors.ink,
    justifyContent: 'center',
    alignItems: 'center',
    overflow: 'hidden',
  },
  route: {
    height: 2,
    borderRadius: 2,
    backgroundColor: colors.surface,
    transform: [{ rotate: '-28deg' }],
  },
  dot: {
    position: 'absolute',
    borderRadius: 99,
    backgroundColor: colors.blue,
  },
  first: { left: '23%', bottom: '26%' },
  second: { right: '22%', top: '24%', backgroundColor: colors.coral },
});

