import { Platform } from 'react-native';

export const colors = {
  canvas: '#FAFAFD',
  surface: '#FFFFFF',
  surfaceTint: '#F5F2FC',
  surfaceViolet: '#EFEBFC',
  ink: '#11184C',
  inkSoft: '#1D2258',
  muted: '#686B8D',
  faint: '#9899AA',
  line: '#E9E8F0',
  lineStrong: '#DCDBEA',
  primary: '#4C3DD4',
  primaryLight: '#574AD6',
  primarySoft: '#ECE8FC',
  navy: '#11184C',
  navyDeep: '#0F164A',
  blue: '#4C3DD4',
  blueSoft: '#ECE8FC',
  coral: '#EF4456',
  coralSoft: '#FFF0F2',
  dock: '#11184C',
  dockActive: '#4C3DD4',
  green: '#49A676',
  greenSoft: '#E8F5EF',
  info: '#5E8EDB',
  infoSoft: '#ECF4FD',
  amber: '#F4B43C',
  whiteMuted: '#C7C9DF',
};

export const radius = {
  small: 12,
  medium: 15,
  large: 20,
  hero: 22,
  pill: 999,
};

export const shadow = Platform.select({
  ios: {
    shadowColor: '#11184C',
    shadowOffset: { width: 0, height: 3 },
    shadowOpacity: 0.07,
    shadowRadius: 12,
  },
  android: { elevation: 2 },
  web: {
    boxShadow: '0 3px 12px rgba(17, 24, 76, 0.07)',
  },
  default: {},
});

export const floatingShadow = Platform.select({
  ios: {
    shadowColor: '#11184C',
    shadowOffset: { width: 0, height: 12 },
    shadowOpacity: 0.13,
    shadowRadius: 30,
  },
  android: { elevation: 8 },
  web: {
    boxShadow: '0 12px 30px rgba(17, 24, 76, 0.13)',
  },
  default: {},
});

export const fonts = {
  regular: 'Poppins_400Regular',
  medium: 'Poppins_500Medium',
  semiBold: 'Poppins_600SemiBold',
  bold: 'Poppins_700Bold',
  extraBold: 'Poppins_800ExtraBold',
};

export const type = {
  hero: {
    fontFamily: fonts.bold,
    fontSize: 32,
    lineHeight: 38,
    letterSpacing: -1.05,
  },
  title: {
    fontFamily: fonts.bold,
    fontSize: 22,
    lineHeight: 28,
    letterSpacing: -0.45,
  },
  section: {
    fontFamily: fonts.bold,
    fontSize: 16,
    lineHeight: 22,
    letterSpacing: -0.18,
  },
  body: {
    fontFamily: fonts.regular,
    fontSize: 13,
    lineHeight: 20,
  },
  label: {
    fontFamily: fonts.semiBold,
    fontSize: 13,
    lineHeight: 18,
  },
  caption: {
    fontFamily: fonts.medium,
    fontSize: 11,
    lineHeight: 16,
  },
};

export const layout = {
  maxWidth: 430,
  gutter: 16,
  dockHeight: 78,
};

export const gradients = {
  navy: ['#141B50', '#0F164A'] as const,
  primary: ['#4C3DD4', '#574AD6'] as const,
  authSky: ['#FCFBFF', '#F2EFFF'] as const,
};
