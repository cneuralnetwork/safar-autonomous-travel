import {
  ActivityIndicator,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  useWindowDimensions,
  View,
} from 'react-native';
import { Image } from 'expo-image';
import { StatusBar } from 'expo-status-bar';
import { Ionicons } from '@expo/vector-icons';
import { LinearGradient } from 'expo-linear-gradient';
import {
  SafeAreaView,
  useSafeAreaInsets,
} from 'react-native-safe-area-context';
import { useAuth } from '@/auth/AuthProvider';
import { colors, fonts, gradients, radius, type } from '@/theme';

const benefits = [
  {
    icon: 'airplane-outline',
    title: 'AI plans everything for you',
    detail: 'Flights, stays, itinerary & more',
  },
  {
    icon: 'options-outline',
    title: 'Best budget, time & comfort',
    detail: 'Optimized for what matters to you',
  },
  {
    icon: 'download-outline',
    title: 'Works with any calendar',
    detail: 'Download a portable .ics file',
  },
] as const;

export function AuthScreen() {
  const { signInWithGoogle, signingIn, error } = useAuth();
  const insets = useSafeAreaInsets();
  const { width } = useWindowDimensions();
  const artwork = require('../../assets/generated/onboarding-panorama.png');
  const googleMark = require('../../assets/generated/google-g.png');
  const frameWidth = Math.min(width, 480);
  const heroHeight = Math.max(480, Math.min(525, frameWidth * 1.255));

  return (
    <LinearGradient
      colors={[colors.canvas, colors.surfaceViolet, colors.navyDeep]}
      locations={[0, 0.56, 0.72]}
      style={styles.screen}
    >
      <StatusBar style="dark" />
      <SafeAreaView edges={['bottom']} style={styles.safe}>
        <ScrollView
          bounces={false}
          contentContainerStyle={styles.scrollContent}
          showsVerticalScrollIndicator={false}
        >
          <View style={[styles.frame, { minHeight: heroHeight }]}>
            <View style={[styles.hero, { height: heroHeight }]}>
              <Image
                accessible={false}
                accessibilityIgnoresInvertColors
                contentFit="cover"
                contentPosition="center"
                source={artwork}
                style={StyleSheet.absoluteFill}
              />
              <View
                style={[
                  styles.heroCopy,
                  { paddingTop: Math.max(76, insets.top + 48) },
                ]}
              >
                <View
                  accessible={false}
                  importantForAccessibility="no-hide-descendants"
                  style={styles.heroPlane}
                >
                  <Ionicons color={colors.navy} name="airplane" size={38} />
                </View>
                <Text accessibilityRole="header" style={styles.brand}>
                  Safar
                </Text>
                <Text style={styles.tagline}>Your AI Travel Agent</Text>
                <Text style={styles.description}>
                  Describe your trip in plain language.{'\n'}
                  We plan the best route, time & budget{'\n'}
                  for you—automatically.
                </Text>
              </View>
            </View>

            <LinearGradient colors={gradients.navy} style={styles.actions}>
              {error ? (
                <View
                  accessibilityLiveRegion="polite"
                  accessibilityRole="alert"
                  style={styles.error}
                >
                  <Ionicons name="alert-circle" size={18} color={colors.coral} />
                  <Text style={styles.errorText}>{error}</Text>
                </View>
              ) : null}

              <Pressable
                accessibilityHint="Opens Google sign-in. Safar never requests access to your calendar account."
                accessibilityLabel="Continue with Google"
                accessibilityRole="button"
                accessibilityState={{ busy: signingIn, disabled: signingIn }}
                disabled={signingIn}
                onPress={() => void signInWithGoogle()}
                style={({ pressed }) => [
                  styles.googleButton,
                  pressed && styles.pressed,
                  signingIn && styles.disabled,
                ]}
              >
                {signingIn ? (
                  <ActivityIndicator
                    accessibilityLabel="Waiting for Google"
                    color={colors.primary}
                  />
                ) : (
                  <Image
                    accessible={false}
                    contentFit="contain"
                    source={googleMark}
                    style={styles.googleMark}
                  />
                )}
                <Text style={styles.googleText}>
                  {signingIn ? 'Waiting for Google…' : 'Continue with Google'}
                </Text>
              </Pressable>

              <View style={styles.benefits}>
                {benefits.map((benefit) => (
                  <View key={benefit.title} style={styles.benefit}>
                    <View style={styles.benefitIcon}>
                      <Ionicons
                        color={colors.surface}
                        name={benefit.icon}
                        size={20}
                      />
                    </View>
                    <View style={styles.benefitCopy}>
                      <Text style={styles.benefitTitle}>{benefit.title}</Text>
                      <Text style={styles.benefitDetail}>{benefit.detail}</Text>
                    </View>
                  </View>
                ))}
              </View>

              <View
                accessibilityElementsHidden
                importantForAccessibility="no-hide-descendants"
                style={styles.pagination}
              >
                <View style={[styles.pageDot, styles.pageDotActive]} />
                <View style={styles.pageDot} />
                <View style={styles.pageDot} />
              </View>
            </LinearGradient>
          </View>
        </ScrollView>
      </SafeAreaView>
    </LinearGradient>
  );
}

const styles = StyleSheet.create({
  screen: {
    flex: 1,
    backgroundColor: colors.navyDeep,
  },
  safe: {
    flex: 1,
    width: '100%',
  },
  scrollContent: {
    flexGrow: 1,
    alignItems: 'center',
  },
  frame: {
    width: '100%',
    maxWidth: 480,
    overflow: 'hidden',
    backgroundColor: colors.navyDeep,
    ...Platform.select({
      web: {
        boxSizing: 'border-box',
      },
      default: {},
    }),
  },
  hero: {
    position: 'relative',
    width: '100%',
    overflow: 'hidden',
    backgroundColor: colors.surfaceViolet,
  },
  heroCopy: {
    alignItems: 'center',
    paddingHorizontal: 24,
  },
  heroPlane: {
    width: 44,
    height: 40,
    alignItems: 'center',
    justifyContent: 'center',
    transform: [{ rotate: '-18deg' }],
  },
  brand: {
    fontFamily: fonts.bold,
    fontSize: 62,
    lineHeight: 69,
    letterSpacing: -2.7,
    color: colors.navy,
    marginTop: 5,
  },
  tagline: {
    fontFamily: fonts.medium,
    fontSize: 18,
    lineHeight: 24,
    color: colors.primary,
    marginTop: -2,
  },
  description: {
    ...type.body,
    color: colors.muted,
    textAlign: 'center',
    marginTop: 20,
  },
  actions: {
    paddingHorizontal: 20,
    paddingTop: 10,
    paddingBottom: 18,
    gap: 11,
  },
  googleButton: {
    minHeight: 54,
    borderRadius: radius.medium,
    backgroundColor: colors.surface,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 12,
  },
  googleMark: {
    width: 22,
    height: 22,
  },
  googleText: {
    fontFamily: fonts.semiBold,
    fontSize: 14,
    lineHeight: 20,
    color: colors.navy,
  },
  benefits: {
    borderRadius: radius.large,
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.08)',
    backgroundColor: 'rgba(255,255,255,0.035)',
    paddingHorizontal: 12,
    paddingVertical: 4,
  },
  benefit: {
    minHeight: 59,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
  },
  benefitIcon: {
    width: 40,
    height: 40,
    borderRadius: radius.small,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: 'rgba(255,255,255,0.1)',
  },
  benefitCopy: {
    flex: 1,
  },
  benefitTitle: {
    fontFamily: fonts.semiBold,
    fontSize: 12,
    lineHeight: 17,
    color: colors.surface,
  },
  benefitDetail: {
    fontFamily: fonts.regular,
    fontSize: 11,
    lineHeight: 16,
    color: colors.whiteMuted,
  },
  error: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: 8,
    padding: 12,
    borderRadius: 14,
    backgroundColor: colors.coralSoft,
  },
  errorText: { ...type.caption, color: '#9B3F37', flex: 1 },
  pagination: {
    minHeight: 20,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 10,
  },
  pageDot: {
    width: 8,
    height: 8,
    borderRadius: radius.pill,
    backgroundColor: 'rgba(255,255,255,0.17)',
  },
  pageDotActive: {
    backgroundColor: colors.surface,
  },
  pressed: { opacity: 0.74, transform: [{ scale: 0.985 }] },
  disabled: { opacity: 0.65 },
});
