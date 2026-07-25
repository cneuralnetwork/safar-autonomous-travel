import { Platform } from 'react-native';
import * as SecureStore from 'expo-secure-store';
import { createClient } from '@supabase/supabase-js';

const supabaseUrl =
  process.env.EXPO_PUBLIC_SUPABASE_URL || 'https://placeholder.supabase.co';
const publishableKey =
  process.env.EXPO_PUBLIC_SUPABASE_PUBLISHABLE_KEY || 'sb_publishable_placeholder';

const SECURE_STORE_CHUNK_SIZE = 1800;

async function removeSecureChunks(key: string) {
  const count = Number(await SecureStore.getItemAsync(`${key}.chunks`)) || 0;
  await Promise.all(
    Array.from({ length: count }, (_, index) =>
      SecureStore.deleteItemAsync(`${key}.${index}`),
    ),
  );
  await SecureStore.deleteItemAsync(`${key}.chunks`);
  await SecureStore.deleteItemAsync(key);
}

const secureStorage = {
  async getItem(key: string) {
    if (Platform.OS === 'web') {
      return globalThis.localStorage?.getItem(key) ?? null;
    }
    const count = Number(await SecureStore.getItemAsync(`${key}.chunks`)) || 0;
    if (!count) {
      return SecureStore.getItemAsync(key);
    }
    const chunks = await Promise.all(
      Array.from({ length: count }, (_, index) =>
        SecureStore.getItemAsync(`${key}.${index}`),
      ),
    );
    return chunks.every((chunk) => chunk !== null) ? chunks.join('') : null;
  },
  async setItem(key: string, value: string) {
    if (Platform.OS === 'web') {
      globalThis.localStorage?.setItem(key, value);
      return;
    }
    await removeSecureChunks(key);
    const chunks = Array.from(
      { length: Math.ceil(value.length / SECURE_STORE_CHUNK_SIZE) },
      (_, index) =>
        value.slice(
          index * SECURE_STORE_CHUNK_SIZE,
          (index + 1) * SECURE_STORE_CHUNK_SIZE,
        ),
    );
    await Promise.all(
      chunks.map((chunk, index) =>
        SecureStore.setItemAsync(`${key}.${index}`, chunk),
      ),
    );
    await SecureStore.setItemAsync(`${key}.chunks`, String(chunks.length));
  },
  async removeItem(key: string) {
    if (Platform.OS === 'web') {
      globalThis.localStorage?.removeItem(key);
      return;
    }
    await removeSecureChunks(key);
  },
};

export const supabase = createClient(supabaseUrl, publishableKey, {
  auth: {
    storage: secureStorage,
    autoRefreshToken: true,
    persistSession: true,
    detectSessionInUrl: false,
  },
});

export const hasSupabaseConfiguration = Boolean(
  process.env.EXPO_PUBLIC_SUPABASE_URL &&
    process.env.EXPO_PUBLIC_SUPABASE_PUBLISHABLE_KEY,
);
