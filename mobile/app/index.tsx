import { ActivityIndicator, StyleSheet, Text, View } from 'react-native';
import { LinearGradient } from 'expo-linear-gradient';
import { useAuth } from '@/auth/AuthProvider';
import { BrandMark } from '@/components/BrandMark';
import { AuthScreen } from '@/screens/AuthScreen';
import { AppShell } from '@/screens/AppShell';
import { colors, fonts, gradients } from '@/theme';

export default function Index() {
  const { session, loading } = useAuth();

  if (loading) {
    return (
      <LinearGradient colors={gradients.authSky} style={styles.loading}>
        <View style={styles.loadingLockup}>
          <BrandMark size={58} />
          <Text accessibilityRole="header" style={styles.loadingBrand}>
            Safar
          </Text>
          <Text style={styles.loadingMessage}>Your next journey starts here</Text>
          <ActivityIndicator
            accessibilityLabel="Loading Safar"
            color={colors.primary}
            size="small"
            style={styles.spinner}
          />
        </View>
      </LinearGradient>
    );
  }

  return session ? <AppShell /> : <AuthScreen />;
}

const styles = StyleSheet.create({
  loading: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    padding: 24,
  },
  loadingLockup: {
    alignItems: 'center',
  },
  loadingBrand: {
    fontFamily: fonts.bold,
    fontSize: 36,
    lineHeight: 43,
    letterSpacing: -1.5,
    color: colors.navy,
    marginTop: 10,
  },
  loadingMessage: {
    fontFamily: fonts.regular,
    fontSize: 12,
    lineHeight: 18,
    color: colors.muted,
    marginTop: 1,
  },
  spinner: {
    marginTop: 22,
  },
});
