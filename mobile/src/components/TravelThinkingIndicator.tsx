import { useEffect, useRef, useState } from 'react';
import {
  Animated,
  Easing,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { colors, fonts, radius, shadow, type } from '@/theme';
import type { AgentPhase } from '@/types';

const travelThoughts: Array<{
  phrase: string;
  icon: keyof typeof Ionicons.glyphMap;
}> = [
  { phrase: 'Reading every detail in your trip brief…', icon: 'sparkles' },
  { phrase: 'Plotting the smoothest route…', icon: 'navigate-outline' },
  { phrase: 'Scanning the skies for better timings…', icon: 'airplane-outline' },
  { phrase: 'Comparing welcoming stays…', icon: 'bed-outline' },
  { phrase: 'Balancing comfort with your budget…', icon: 'wallet-outline' },
  { phrase: 'Pinning memorable stops along the way…', icon: 'map-outline' },
  { phrase: 'Shaping each day of your itinerary…', icon: 'calendar-outline' },
  { phrase: 'Checking the little travel details…', icon: 'compass-outline' },
];

const phaseLead: Partial<Record<AgentPhase, string>> = {
  interpreting: 'Understanding your journey',
  planning: 'Designing your journey',
  executing: 'Searching the travel world',
  replanning: 'Finding another way',
  finalizing: 'Packing your final plan',
};

function randomNextIndex(current: number) {
  if (travelThoughts.length < 2) return 0;
  const offset = 1 + Math.floor(Math.random() * (travelThoughts.length - 1));
  return (current + offset) % travelThoughts.length;
}

export function TravelThinkingIndicator({
  phase = 'interpreting',
}: {
  phase?: AgentPhase;
}) {
  const [thoughtIndex, setThoughtIndex] = useState(() =>
    Math.floor(Math.random() * travelThoughts.length),
  );
  const orbit = useRef(new Animated.Value(0)).current;
  const breathe = useRef(new Animated.Value(0)).current;
  const thought = travelThoughts[thoughtIndex] ?? travelThoughts[0]!;

  useEffect(() => {
    const orbitLoop = Animated.loop(
      Animated.timing(orbit, {
        toValue: 1,
        duration: 2200,
        easing: Easing.linear,
        useNativeDriver: true,
      }),
    );
    const breatheLoop = Animated.loop(
      Animated.sequence([
        Animated.timing(breathe, {
          toValue: 1,
          duration: 760,
          easing: Easing.inOut(Easing.quad),
          useNativeDriver: true,
        }),
        Animated.timing(breathe, {
          toValue: 0,
          duration: 760,
          easing: Easing.inOut(Easing.quad),
          useNativeDriver: true,
        }),
      ]),
    );
    orbitLoop.start();
    breatheLoop.start();
    const thoughtTimer = setInterval(() => {
      setThoughtIndex((current) => randomNextIndex(current));
    }, 1850);

    return () => {
      clearInterval(thoughtTimer);
      orbitLoop.stop();
      breatheLoop.stop();
    };
  }, [breathe, orbit]);

  const rotate = orbit.interpolate({
    inputRange: [0, 1],
    outputRange: ['0deg', '360deg'],
  });
  const scale = breathe.interpolate({
    inputRange: [0, 1],
    outputRange: [0.94, 1.04],
  });
  const opacity = breathe.interpolate({
    inputRange: [0, 1],
    outputRange: [0.72, 1],
  });

  return (
    <View
      style={styles.card}
      accessibilityRole="progressbar"
      accessibilityLiveRegion="polite"
      accessibilityLabel={`${phaseLead[phase] ?? 'Safar is thinking'}. ${thought.phrase}`}
    >
      <Animated.View style={[styles.iconStage, { transform: [{ scale }] }]}>
        <Animated.View
          pointerEvents="none"
          style={[styles.orbit, { transform: [{ rotate }] }]}
        >
          <View style={styles.orbitDot} />
        </Animated.View>
        <Ionicons name={thought.icon} color={colors.primary} size={20} />
      </Animated.View>
      <View style={styles.copy}>
        <Text style={styles.lead}>{phaseLead[phase] ?? 'Safar is thinking'}</Text>
        <Animated.Text style={[styles.phrase, { opacity }]}>
          {thought.phrase}
        </Animated.Text>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    minHeight: 76,
    padding: 12,
    borderRadius: 18,
    borderCurve: 'continuous',
    flexDirection: 'row',
    alignItems: 'center',
    gap: 11,
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.line,
    ...shadow,
  },
  iconStage: {
    width: 46,
    height: 46,
    borderRadius: radius.pill,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: colors.primarySoft,
  },
  orbit: {
    position: 'absolute',
    width: 40,
    height: 40,
    borderRadius: radius.pill,
    borderWidth: 1,
    borderColor: '#D8D1F7',
  },
  orbitDot: {
    position: 'absolute',
    top: -2,
    left: 17,
    width: 5,
    height: 5,
    borderRadius: radius.pill,
    backgroundColor: colors.primary,
  },
  copy: {
    flex: 1,
    minWidth: 0,
    gap: 2,
  },
  lead: {
    ...type.label,
    color: colors.ink,
  },
  phrase: {
    fontFamily: fonts.regular,
    fontSize: 11,
    lineHeight: 17,
    color: colors.muted,
  },
});
