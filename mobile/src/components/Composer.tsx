import { forwardRef, useState } from 'react';
import {
  ActivityIndicator,
  Pressable,
  StyleSheet,
  TextInput,
  type TextInput as TextInputType,
  View,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import * as Haptics from 'expo-haptics';
import { colors, radius, shadow, type } from '@/theme';

interface ComposerProps {
  value?: string;
  onChangeText?: (value: string) => void;
  onSend: (text: string) => Promise<void>;
  busy?: boolean;
  placeholder?: string;
}

export const Composer = forwardRef<TextInputType, ComposerProps>(function Composer(
  { value, onChangeText, onSend, busy = false, placeholder = 'Describe your trip…' },
  ref,
) {
  const [internal, setInternal] = useState('');
  const text = value ?? internal;
  const setText = onChangeText ?? setInternal;

  const submit = async () => {
    const cleaned = text.trim();
    if (!cleaned || busy) return;
    await Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
    setText('');
    await onSend(cleaned);
  };

  return (
    <View style={styles.shell}>
      <TextInput
        ref={ref}
        value={text}
        onChangeText={setText}
        placeholder={placeholder}
        placeholderTextColor="#9A9D9F"
        multiline
        maxLength={4000}
        returnKeyType="send"
        blurOnSubmit={false}
        onSubmitEditing={() => void submit()}
        style={styles.input}
        accessibilityLabel="Trip message"
      />
      <Pressable
        onPress={() => void submit()}
        disabled={!text.trim() || busy}
        style={({ pressed }) => [
          styles.send,
          (!text.trim() || busy) && styles.sendDisabled,
          pressed && styles.pressed,
        ]}
        accessibilityRole="button"
        accessibilityLabel="Send message"
      >
        {busy ? (
          <ActivityIndicator color={colors.surface} size="small" />
        ) : (
          <Ionicons name="arrow-up" color={colors.surface} size={21} />
        )}
      </Pressable>
    </View>
  );
});

const styles = StyleSheet.create({
  shell: {
    minHeight: 62,
    maxHeight: 132,
    flexDirection: 'row',
    alignItems: 'flex-end',
    gap: 10,
    padding: 8,
    paddingLeft: 18,
    borderRadius: 24,
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.line,
    ...shadow,
  },
  input: {
    ...type.body,
    flex: 1,
    minHeight: 44,
    maxHeight: 112,
    paddingTop: 11,
    paddingBottom: 10,
    color: colors.ink,
  },
  send: {
    width: 46,
    height: 46,
    borderRadius: radius.pill,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: colors.ink,
  },
  sendDisabled: { backgroundColor: '#B7B9B8' },
  pressed: { opacity: 0.78, transform: [{ scale: 0.96 }] },
});

