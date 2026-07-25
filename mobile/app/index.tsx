import { ActivityIndicator, StyleSheet, View } from 'react-native';
import { useAuth } from '@/auth/AuthProvider';
import { AuthScreen } from '@/screens/AuthScreen';
import { AppShell } from '@/screens/AppShell';
import { colors } from '@/theme';

export default function Index() {
  const { session, loading } = useAuth();

  if (loading) {
    return (
      <View style={styles.loading}>
        <ActivityIndicator color={colors.blue} size="large" />
      </View>
    );
  }

  return session ? <AppShell /> : <AuthScreen />;
}

const styles = StyleSheet.create({
  loading: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: colors.canvas,
  },
});

