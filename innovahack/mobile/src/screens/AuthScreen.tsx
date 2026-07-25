import {
  ActivityIndicator,
  Platform,
  Pressable,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import { Image } from 'expo-image';
import { Ionicons } from '@expo/vector-icons';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useAuth } from '@/auth/AuthProvider';
import { BrandMark } from '@/components/BrandMark';
import { colors, radius, shadow, type } from '@/theme';

export function AuthScreen() {
  const { signInWithGoogle, signingIn, error } = useAuth();
  const artwork = require('../../assets/generated/auth-journey.png');
  return (
    <SafeAreaView style={styles.safe}>
      <View style={styles.top}>
        <BrandMark size={46} />
        <Text style={styles.brand}>Safar</Text>
      </View>
      <View style={styles.hero}>
        <Image source={artwork} style={styles.artwork} contentFit="cover" />
      </View>
      <View style={styles.copy}>
        <Text style={styles.eyebrow}>An autonomous travel agent</Text>
        <Text style={styles.title}>A complete trip, from one message.</Text>
        <Text style={styles.body}>
          Tell Safar where you want to go. It compares flights and stays, builds the
          itinerary, and asks before touching your calendar.
        </Text>
      </View>
      <View style={styles.actions}>
        {error ? (
          <View style={styles.error}>
            <Ionicons name="alert-circle" size={18} color={colors.coral} />
            <Text style={styles.errorText}>{error}</Text>
          </View>
        ) : null}
        <Pressable
          accessibilityRole="button"
          accessibilityLabel="Continue with Google"
          disabled={signingIn}
          onPress={() => void signInWithGoogle()}
          style={({ pressed }) => [
            styles.googleButton,
            pressed && styles.pressed,
            signingIn && styles.disabled,
          ]}
        >
          {signingIn ? (
            <ActivityIndicator color={colors.ink} />
          ) : (
            <View style={styles.googleMark}>
              <Text style={styles.googleLetter}>G</Text>
            </View>
          )}
          <Text style={styles.googleText}>
            {signingIn ? 'Waiting for Google…' : 'Continue with Google'}
          </Text>
        </Pressable>
        <Text style={styles.legal}>
          Google is the only sign-in method. Calendar access is requested separately,
          only when you approve an itinerary.
        </Text>
      </View>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: {
    flex: 1,
    width:
      Platform.OS === 'web'
        ? ('calc(100% - 40px)' as unknown as number)
        : '100%',
    maxWidth: 480,
    alignSelf: 'center',
    paddingHorizontal: 20,
    paddingBottom: 16,
    backgroundColor: colors.canvas,
    ...Platform.select({
      web: {
        boxSizing: 'border-box',
        minHeight: '100vh' as unknown as number,
      },
      default: {},
    }),
  },
  top: { flexDirection: 'row', alignItems: 'center', gap: 11, paddingTop: 8 },
  brand: { ...type.section, color: colors.ink },
  hero: {
    flex: 1,
    minHeight: 250,
    maxHeight: 390,
    marginTop: 22,
    borderRadius: 30,
    overflow: 'hidden',
    backgroundColor: colors.surface,
    ...shadow,
  },
  artwork: { width: '100%', height: '100%' },
  copy: { paddingTop: 28, gap: 10 },
  eyebrow: {
    ...type.caption,
    color: colors.blue,
    textTransform: 'uppercase',
    letterSpacing: 1,
  },
  title: { ...type.hero, color: colors.ink, maxWidth: 360 },
  body: { ...type.body, color: colors.muted, maxWidth: 390 },
  actions: { gap: 12, paddingTop: 24 },
  googleButton: {
    minHeight: 58,
    borderRadius: radius.medium,
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: '#D6D8D6',
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 12,
    ...shadow,
  },
  googleMark: {
    width: 27,
    height: 27,
    borderRadius: 9,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: colors.canvas,
  },
  googleLetter: { fontSize: 16, fontWeight: '800', color: '#4285F4' },
  googleText: { ...type.label, color: colors.ink },
  legal: { ...type.caption, color: colors.muted, textAlign: 'center', paddingHorizontal: 12 },
  error: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: 8,
    padding: 12,
    borderRadius: 14,
    backgroundColor: colors.coralSoft,
  },
  errorText: { ...type.caption, color: '#9B3F37', flex: 1 },
  pressed: { opacity: 0.74, transform: [{ scale: 0.985 }] },
  disabled: { opacity: 0.65 },
});
