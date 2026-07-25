import { Platform } from 'react-native';

export const colors = {
  canvas: '#EFEFED',
  surface: '#FFFFFF',
  ink: '#17181A',
  muted: '#777B7E',
  line: '#E1E2DF',
  blue: '#159FDC',
  blueSoft: '#E8F6FC',
  coral: '#EA695C',
  coralSoft: '#FBECEA',
  dock: '#202124',
  dockActive: '#3A3B3E',
  green: '#30A46C',
  amber: '#D28B22',
};

export const radius = {
  small: 12,
  medium: 18,
  large: 24,
  pill: 999,
};

export const shadow = Platform.select({
  ios: {
    shadowColor: '#111111',
    shadowOffset: { width: 0, height: 6 },
    shadowOpacity: 0.07,
    shadowRadius: 18,
  },
  android: { elevation: 3 },
  default: {},
});

export const type = {
  hero: { fontSize: 34, lineHeight: 38, fontWeight: '800' as const, letterSpacing: -1.1 },
  title: { fontSize: 24, lineHeight: 28, fontWeight: '800' as const, letterSpacing: -0.5 },
  section: { fontSize: 19, lineHeight: 24, fontWeight: '700' as const },
  body: { fontSize: 16, lineHeight: 22, fontWeight: '400' as const },
  label: { fontSize: 14, lineHeight: 18, fontWeight: '600' as const },
  caption: { fontSize: 12, lineHeight: 16, fontWeight: '500' as const },
};
