import {
  createContext,
  type PropsWithChildren,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from 'react';
import type { Session } from '@supabase/supabase-js';
import * as Crypto from 'expo-crypto';
import * as WebBrowser from 'expo-web-browser';
import { authApi } from '@/lib/api';
import { supabase } from '@/lib/supabase';

interface AuthContextValue {
  session: Session | null;
  loading: boolean;
  signingIn: boolean;
  error: string | null;
  signInWithGoogle: () => Promise<void>;
  signOut: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

function randomProof(bytes: Uint8Array): string {
  return Array.from(bytes, (value) => value.toString(16).padStart(2, '0')).join('');
}

const wait = (milliseconds: number) =>
  new Promise<void>((resolve) => {
    setTimeout(resolve, milliseconds);
  });

export function AuthProvider({ children }: PropsWithChildren) {
  const [session, setSession] = useState<Session | null>(null);
  const [loading, setLoading] = useState(true);
  const [signingIn, setSigningIn] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void WebBrowser.warmUpAsync();
    void supabase.auth.getSession().then(({ data }) => {
      setSession(data.session);
      setLoading(false);
    });
    const {
      data: { subscription },
    } = supabase.auth.onAuthStateChange((_event, nextSession) => {
      setSession(nextSession);
      setLoading(false);
    });
    return () => {
      subscription.unsubscribe();
      void WebBrowser.coolDownAsync();
    };
  }, []);

  const signInWithGoogle = useCallback(async () => {
    setSigningIn(true);
    setError(null);
    try {
      const proof = randomProof(await Crypto.getRandomBytesAsync(32));
      const proofHash = await Crypto.digestStringAsync(
        Crypto.CryptoDigestAlgorithm.SHA256,
        proof,
      );
      const attempt = await authApi.start(proofHash);
      await WebBrowser.openBrowserAsync(attempt.authorization_url, {
        presentationStyle: WebBrowser.WebBrowserPresentationStyle.PAGE_SHEET,
        controlsColor: '#17181A',
      });
      for (let poll = 0; poll < attempt.expires_in; poll += 1) {
        const result = await authApi.exchange(attempt.attempt_id, proof);
        if (result.status === 'completed' && result.session) {
          const { error: sessionError } = await supabase.auth.setSession({
            access_token: result.session.access_token,
            refresh_token: result.session.refresh_token,
          });
          if (sessionError) throw sessionError;
          return;
        }
        if (result.status === 'failed') {
          throw new Error(result.error || 'Google sign-in was not completed.');
        }
        await wait(1000);
      }
      throw new Error('Google sign-in expired. Please try again.');
    } catch (signInError) {
      setError(signInError instanceof Error ? signInError.message : 'Could not sign in.');
    } finally {
      setSigningIn(false);
    }
  }, []);

  const signOut = useCallback(async () => {
    setError(null);
    await supabase.auth.signOut();
  }, []);

  const value = useMemo(
    () => ({ session, loading, signingIn, error, signInWithGoogle, signOut }),
    [session, loading, signingIn, error, signInWithGoogle, signOut],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext);
  if (!value) {
    throw new Error('useAuth must be used inside AuthProvider');
  }
  return value;
}

