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
import { colors, floatingShadow, radius, type } from '@/theme';

interface ComposerProps {
  value?: string;
  onChangeText?: (value: string) => void;
  onSend: (text: string) => Promise<void>;
  busy?: boolean;
  placeholder?: string;
  variant?: 'prompt' | 'compact';
}

export const Composer = forwardRef<TextInputType, ComposerProps>(function Composer(
  {
    value,
    onChangeText,
    onSend,
    busy = false,
    placeholder = 'Describe your trip…',
    variant = 'prompt',
  },
  ref,
) {
  const [internal, setInternal] = useState('');
  const text = value ?? internal;
  const setText = onChangeText ?? setInternal;
  const isCompact = variant === 'compact';
  const isEmpty = !text.trim();

  const submit = async () => {
    const cleaned = text.trim();
    if (!cleaned || busy) return;
    await Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
    setText('');
    await onSend(cleaned);
  };

  return (
    <View style={[styles.frame, isCompact && styles.frameCompact]}>
      <View style={[styles.shell, isCompact && styles.shellCompact]}>
        {!isCompact ? (
          <View style={styles.promptMark}>
            <Ionicons name="sparkles" color={colors.primary} size={21} />
          </View>
        ) : null}
        <TextInput
          ref={ref}
          value={text}
          onChangeText={setText}
          placeholder={placeholder}
          placeholderTextColor={colors.muted}
          multiline
          maxLength={4000}
          returnKeyType="send"
          blurOnSubmit={false}
          onSubmitEditing={() => void submit()}
          textAlignVertical="top"
          style={[styles.input, isCompact && styles.inputCompact]}
          accessibilityLabel="Trip message"
        />
        <Pressable
          onPress={() => void submit()}
          disabled={isEmpty || busy}
          style={({ pressed }) => [
            styles.send,
            isCompact && styles.sendCompact,
            isEmpty && styles.sendDisabled,
            pressed && styles.pressed,
          ]}
          accessibilityRole="button"
          accessibilityLabel="Send message"
          accessibilityState={{ disabled: isEmpty || busy, busy }}
        >
          {busy ? (
            <ActivityIndicator color={colors.surface} size="small" />
          ) : (
            <Ionicons
              name="arrow-up"
              color={colors.surface}
              size={isCompact ? 20 : 22}
            />
          )}
        </Pressable>
      </View>
    </View>
  );
});

const styles = StyleSheet.create({
  frame: {
    width: '100%',
    padding: 5,
    borderRadius: 30,
    backgroundColor: colors.primarySoft,
    borderWidth: 1,
    borderColor: colors.line,
    ...floatingShadow,
  },
  frameCompact: {
    padding: 3,
    borderRadius: 25,
  },
  shell: {
    minHeight: 104,
    maxHeight: 170,
    flexDirection: 'row',
    alignItems: 'flex-end',
    gap: 8,
    padding: 11,
    paddingLeft: 13,
    borderRadius: 25,
    backgroundColor: colors.surfaceViolet,
    borderWidth: 1,
    borderColor: colors.line,
  },
  shellCompact: {
    minHeight: 58,
    maxHeight: 132,
    padding: 7,
    paddingLeft: 14,
    borderRadius: 22,
    backgroundColor: colors.surface,
  },
  promptMark: {
    width: 24,
    height: 42,
    paddingTop: 7,
    alignSelf: 'flex-start',
    alignItems: 'flex-start',
  },
  input: {
    ...type.body,
    flex: 1,
    minHeight: 78,
    maxHeight: 142,
    paddingTop: 7,
    paddingBottom: 7,
    color: colors.ink,
    fontSize: 14,
    lineHeight: 22,
  },
  inputCompact: {
    minHeight: 42,
    maxHeight: 112,
    paddingTop: 10,
    paddingBottom: 8,
    fontSize: type.body.fontSize,
    lineHeight: type.body.lineHeight,
  },
  send: {
    width: 48,
    height: 48,
    borderRadius: radius.pill,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: colors.primary,
  },
  sendCompact: {
    width: 42,
    height: 42,
  },
  sendDisabled: { opacity: 0.38 },
  pressed: { opacity: 0.78, transform: [{ scale: 0.96 }] },
});
