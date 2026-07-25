import {
  type ReactNode,
  type RefObject,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import {
  ActivityIndicator,
  Alert,
  Keyboard,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Switch,
  Text,
  TextInput,
  View,
} from 'react-native';
import { Image, type ImageSource } from 'expo-image';
import { LinearGradient } from 'expo-linear-gradient';
import { StatusBar } from 'expo-status-bar';
import { Ionicons } from '@expo/vector-icons';
import { SafeAreaView, useSafeAreaInsets } from 'react-native-safe-area-context';
import * as WebBrowser from 'expo-web-browser';
import { useAuth } from '@/auth/AuthProvider';
import { api, ApiError } from '@/lib/api';
import { supabase } from '@/lib/supabase';
import {
  tripImageAssignments,
  tripImageForKey,
} from '@/lib/tripImages';
import { BottomDock, type TabKey } from '@/components/BottomDock';
import { BrandMark } from '@/components/BrandMark';
import { Composer } from '@/components/Composer';
import { ItineraryMap } from '@/components/ItineraryMap';
import { MessageRenderer } from '@/components/MessageRenderer';
import {
  colors,
  floatingShadow,
  fonts,
  gradients,
  layout,
  radius,
  shadow,
  type,
} from '@/theme';
import type {
  AgentEvent,
  ChatMessage,
  Conversation,
  ConversationSnapshot,
  ItineraryDay,
  OperationEvent,
  RunState,
  TravelConstraints,
} from '@/types';
import {
  formatCurrency,
  formatDate,
  formatTime,
  relativeDate,
} from '@/utils/format';

const wait = (milliseconds: number) =>
  new Promise<void>((resolve) => {
    setTimeout(resolve, milliseconds);
  });

type TripSection = 'plan' | 'itinerary' | 'map' | 'details';

const tripSections: Array<{ key: TripSection; label: string }> = [
  { key: 'plan', label: 'Plan' },
  { key: 'itinerary', label: 'Itinerary' },
  { key: 'map', label: 'Map' },
  { key: 'details', label: 'Details' },
];

export function AppShell() {
  const { session, signOut } = useAuth();
  const insets = useSafeAreaInsets();
  const accessToken = session?.access_token ?? '';
  const [tab, setTab] = useState<TabKey>('plan');
  const [tripSection, setTripSection] = useState<TripSection>('plan');
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [snapshot, setSnapshot] = useState<ConversationSnapshot | null>(null);
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [sending, setSending] = useState(false);
  const [approvalBusy, setApprovalBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [composerText, setComposerText] = useState('');
  const [editingApproval, setEditingApproval] = useState(false);
  const [resilienceDemo, setResilienceDemo] = useState(false);
  const composerRef = useRef<TextInput>(null);
  const eventCursorRef = useRef(0);
  const assignedTripImages = useMemo(
    () =>
      tripImageAssignments(
        conversations.map((conversation) => conversation.id),
      ),
    [conversations],
  );

  const mergeEvents = useCallback((incoming: AgentEvent[]) => {
    if (!incoming.length) return;
    setEvents((current) => {
      const byId = new Map(current.map((event) => [event.id, event]));
      incoming.forEach((event) => byId.set(event.id, event));
      return [...byId.values()].sort((left, right) => left.id - right.id);
    });
    eventCursorRef.current = Math.max(
      eventCursorRef.current,
      ...incoming.map((event) => event.id),
    );
  }, []);

  const refreshConversations = useCallback(async () => {
    if (!accessToken) return;
    try {
      setConversations(await api.listConversations(accessToken));
    } catch (refreshError) {
      setError(messageForError(refreshError));
    }
  }, [accessToken]);

  const refreshSnapshot = useCallback(async () => {
    if (!accessToken || !conversationId) return;
    try {
      const next = await api.getConversation(accessToken, conversationId);
      setSnapshot(next);
      if (next.active_run) {
        const eventPage = await api.listRunEvents(
          accessToken,
          next.active_run.id,
          eventCursorRef.current,
        );
        mergeEvents(eventPage.items);
      }
      setError(null);
    } catch (refreshError) {
      setError(messageForError(refreshError));
    }
  }, [accessToken, conversationId, mergeEvents]);

  useEffect(() => {
    void refreshConversations();
  }, [refreshConversations]);

  useEffect(() => {
    if (!conversationId) {
      setSnapshot(null);
      setEvents([]);
      eventCursorRef.current = 0;
      return;
    }
    setEvents([]);
    eventCursorRef.current = 0;
    void refreshSnapshot();
    let reconcileTimer: ReturnType<typeof setTimeout> | null = null;
    const reconcile = () => {
      if (reconcileTimer) return;
      reconcileTimer = setTimeout(() => {
        reconcileTimer = null;
        void refreshSnapshot();
      }, 180);
    };
    const channel = supabase
      .channel(`safar-agent:${conversationId}`)
      .on(
        'postgres_changes',
        {
          event: 'INSERT',
          schema: 'public',
          table: 'agent_events',
          filter: `conversation_id=eq.${conversationId}`,
        },
        (payload) => {
          mergeEvents([payload.new as AgentEvent]);
          reconcile();
        },
      )
      .on(
        'postgres_changes',
        {
          event: '*',
          schema: 'public',
          table: 'runs',
          filter: `conversation_id=eq.${conversationId}`,
        },
        reconcile,
      )
      .on(
        'postgres_changes',
        {
          event: 'INSERT',
          schema: 'public',
          table: 'messages',
          filter: `conversation_id=eq.${conversationId}`,
        },
        reconcile,
      )
      .subscribe((status) => {
        if (status === 'SUBSCRIBED') reconcile();
      });
    const fallbackPoll = setInterval(() => void refreshSnapshot(), 15_000);
    return () => {
      if (reconcileTimer) clearTimeout(reconcileTimer);
      clearInterval(fallbackPoll);
      void supabase.removeChannel(channel);
    };
  }, [conversationId, mergeEvents, refreshSnapshot]);

  const sendMessage = useCallback(
    async (text: string) => {
      if (!accessToken) return;
      setSending(true);
      setError(null);
      try {
        if (!conversationId) {
          const created = await api.createConversation(
            accessToken,
            text,
            resilienceDemo,
          );
          setConversationId(created.conversation.id);
          setSnapshot(created);
          setTripSection('plan');
        } else if (editingApproval && snapshot?.active_run?.approval) {
          await api.resolveApproval(
            accessToken,
            snapshot.active_run.id,
            snapshot.active_run.approval,
            'edit',
            text,
          );
          setEditingApproval(false);
        } else {
          await api.sendMessage(accessToken, conversationId, text, resilienceDemo);
          await refreshSnapshot();
        }
        await refreshConversations();
      } catch (sendError) {
        setComposerText(text);
        setError(messageForError(sendError));
      } finally {
        setSending(false);
      }
    },
    [
      accessToken,
      conversationId,
      editingApproval,
      refreshConversations,
      refreshSnapshot,
      resilienceDemo,
      snapshot?.active_run,
    ],
  );

  const connectCalendar = useCallback(async () => {
    const connection = await api.connectCalendar(accessToken);
    await WebBrowser.openBrowserAsync(connection.authorization_url, {
      presentationStyle: WebBrowser.WebBrowserPresentationStyle.PAGE_SHEET,
      controlsColor: colors.ink,
    });
    for (let count = 0; count < 90; count += 1) {
      const current = await api.calendarStatus(accessToken);
      if (current.connected) return true;
      if (current.authorization_status === 'failed') {
        throw new Error(current.error || 'Calendar permission was not granted.');
      }
      await wait(1000);
    }
    return false;
  }, [accessToken]);

  const resolveApproval = useCallback(
    async (decision: 'approve' | 'edit' | 'cancel') => {
      const run = snapshot?.active_run;
      if (!run?.approval) return;
      if (decision === 'edit') {
        setEditingApproval(true);
        setComposerText('Change the plan: ');
        setTripSection('plan');
        requestAnimationFrame(() => composerRef.current?.focus());
        return;
      }
      setApprovalBusy(true);
      setEditingApproval(false);
      setError(null);
      try {
        await api.resolveApproval(accessToken, run.id, run.approval, decision);
      } catch (approvalError) {
        if (
          decision === 'approve' &&
          approvalError instanceof ApiError &&
          approvalError.status === 409
        ) {
          try {
            const connected = await connectCalendar();
            if (!connected) {
              throw new Error('Calendar connection was not completed.');
            }
            await api.resolveApproval(
              accessToken,
              run.id,
              run.approval,
              'approve',
            );
          } catch (calendarError) {
            setError(messageForError(calendarError));
          }
        } else {
          setError(messageForError(approvalError));
        }
      } finally {
        setApprovalBusy(false);
        await refreshSnapshot();
        await refreshConversations();
      }
    },
    [
      accessToken,
      connectCalendar,
      refreshConversations,
      refreshSnapshot,
      snapshot?.active_run,
    ],
  );

  const openConversation = useCallback((id: string) => {
    setConversationId(id);
    setTripSection('plan');
    setTab('plan');
  }, []);

  const newTrip = useCallback(() => {
    Keyboard.dismiss();
    setConversationId(null);
    setSnapshot(null);
    setComposerText('');
    setEditingApproval(false);
    setTripSection('plan');
    setTab('plan');
  }, []);

  const darkStatusBar = tab === 'plan' && !snapshot;
  const dockBottom = Math.max(10, insets.bottom + 2);
  const displayName =
    session?.user.user_metadata.full_name ||
    session?.user.user_metadata.name ||
    'Traveller';

  return (
    <View style={styles.webCanvas}>
      <SafeAreaView
        style={[styles.safe, darkStatusBar && styles.safeDark]}
        edges={['top']}
      >
        <StatusBar style={darkStatusBar ? 'light' : 'dark'} />
        <View style={styles.content}>
          {tab === 'plan' && !snapshot ? (
            <HomeScreen
              name={displayName}
              conversations={conversations}
              activeId={conversationId}
              onOpen={openConversation}
              onTab={setTab}
              onSend={sendMessage}
              sending={sending}
              error={error}
              composerText={composerText}
              setComposerText={setComposerText}
              composerRef={composerRef}
              resilienceDemo={resilienceDemo}
              setResilienceDemo={setResilienceDemo}
            />
          ) : null}
          {tab === 'plan' && snapshot ? (
            <TripWorkspace
              snapshot={snapshot}
              image={
                assignedTripImages.get(snapshot.conversation.id) ??
                tripImageForKey(snapshot.conversation.id)
              }
              section={tripSection}
              setSection={setTripSection}
              onBack={newTrip}
              onSend={sendMessage}
              sending={sending}
              error={error}
              composerText={composerText}
              setComposerText={setComposerText}
              composerRef={composerRef}
              onApproval={resolveApproval}
              approvalBusy={approvalBusy}
              editingApproval={editingApproval}
            />
          ) : null}
          {tab === 'trips' ? (
            <TripsScreen
              conversations={conversations}
              activeId={conversationId}
              onOpen={openConversation}
              onNewTrip={newTrip}
            />
          ) : null}
          {tab === 'activity' ? (
            <ActivityScreen
              events={events}
              messages={snapshot?.messages ?? []}
              tripTitle={snapshot?.conversation.title}
            />
          ) : null}
          {tab === 'profile' ? (
            <ProfileScreen
              accessToken={accessToken}
              name={displayName}
              email={session?.user.email || ''}
              avatar={session?.user.user_metadata.avatar_url}
              resilienceDemo={resilienceDemo}
              setResilienceDemo={setResilienceDemo}
              connectCalendar={connectCalendar}
              signOut={signOut}
            />
          ) : null}
        </View>
        <View style={[styles.dockWrap, { bottom: dockBottom }]}>
          <BottomDock active={tab} onChange={setTab} onCreate={newTrip} />
        </View>
      </SafeAreaView>
    </View>
  );
}

function HomeScreen({
  name,
  conversations,
  activeId,
  onOpen,
  onTab,
  onSend,
  sending,
  error,
  composerText,
  setComposerText,
  composerRef,
  resilienceDemo,
  setResilienceDemo,
}: {
  name: string;
  conversations: Conversation[];
  activeId: string | null;
  onOpen: (id: string) => void;
  onTab: (tab: TabKey) => void;
  onSend: (text: string) => Promise<void>;
  sending: boolean;
  error: string | null;
  composerText: string;
  setComposerText: (text: string) => void;
  composerRef: RefObject<TextInput | null>;
  resilienceDemo: boolean;
  setResilienceDemo: (value: boolean) => void;
}) {
  const firstName = name.trim().split(/\s+/)[0] || 'Traveller';
  const images = tripImageAssignments(
    conversations.map((conversation) => conversation.id),
  );
  const quickActions: Array<{
    label: string;
    icon: keyof typeof Ionicons.glyphMap;
    action: () => void;
  }> = [
    { label: 'Trips', icon: 'briefcase-outline', action: () => onTab('trips') },
    { label: 'Calendar', icon: 'calendar-outline', action: () => onTab('profile') },
    { label: 'Preferences', icon: 'heart-outline', action: () => onTab('profile') },
    {
      label: 'Explore',
      icon: 'compass-outline',
      action: () => composerRef.current?.focus(),
    },
  ];
  return (
    <View style={styles.homeScreen}>
      <LinearGradient colors={gradients.navy} style={styles.homeHeader}>
        <View style={styles.homeTopRow}>
          <Pressable
            accessibilityRole="button"
            accessibilityLabel="View trips"
            onPress={() => onTab('trips')}
            style={({ pressed }) => [
              styles.headerCircleDark,
              pressed && styles.pressed,
            ]}
          >
            <Ionicons name="menu" size={22} color={colors.surface} />
          </Pressable>
          <View style={styles.greeting}>
            <Text style={styles.greetingTitle}>Good morning, {firstName} 👋</Text>
            <Text style={styles.greetingBody}>Where do you want to go next?</Text>
          </View>
          <Pressable
            accessibilityRole="button"
            accessibilityLabel="View activity"
            onPress={() => onTab('activity')}
            style={({ pressed }) => [
              styles.headerCircleDark,
              pressed && styles.pressed,
            ]}
          >
            <Ionicons name="notifications-outline" size={21} color={colors.surface} />
            <View style={styles.notificationDot} />
          </Pressable>
        </View>
      </LinearGradient>
      <ScrollView
        style={styles.homeScroll}
        contentContainerStyle={styles.homeScrollContent}
        keyboardShouldPersistTaps="handled"
        showsVerticalScrollIndicator={false}
      >
        <View style={styles.homePrompt}>
          {error ? <InlineError text={error} /> : null}
          <Composer
            ref={composerRef}
            value={composerText}
            onChangeText={setComposerText}
            onSend={onSend}
            busy={sending}
            variant="prompt"
            placeholder="Describe your dream trip, budget and dates…"
          />
        </View>
        <View style={styles.quickGrid}>
          {quickActions.map((item) => (
            <Pressable
              key={item.label}
              onPress={item.action}
              style={({ pressed }) => [
                styles.quickAction,
                pressed && styles.pressed,
              ]}
            >
              <View style={styles.quickIcon}>
                <Ionicons name={item.icon} size={22} color={colors.primary} />
              </View>
              <Text style={styles.quickLabel}>{item.label}</Text>
            </Pressable>
          ))}
        </View>

        <SectionHeader
          title="Upcoming trips"
          action="View all"
          onPress={() => onTab('trips')}
        />
        <View style={styles.homeTripList}>
          {conversations.length ? (
            conversations.slice(0, 2).map((conversation) => (
              <Pressable
                key={conversation.id}
                onPress={() => onOpen(conversation.id)}
                style={({ pressed }) => [
                  styles.homeTripCard,
                  conversation.id === activeId && styles.homeTripCardActive,
                  pressed && styles.pressed,
                ]}
              >
                <Image
                  source={
                    images.get(conversation.id) ??
                    tripImageForKey(conversation.id)
                  }
                  style={styles.homeTripImage}
                  contentFit="cover"
                  transition={180}
                />
                <View style={styles.homeTripCopy}>
                  <View style={styles.tripTitleRow}>
                    <Text style={styles.homeTripTitle} numberOfLines={1}>
                      {conversation.title}
                    </Text>
                    <View
                      style={[
                        styles.statusPill,
                        conversation.id === activeId && styles.statusPillActive,
                      ]}
                    >
                      <Text
                        style={[
                          styles.statusText,
                          conversation.id === activeId && styles.statusTextActive,
                        ]}
                      >
                        {conversation.id === activeId ? 'Planning' : 'Saved'}
                      </Text>
                    </View>
                  </View>
                  <Text style={styles.homeTripMeta} numberOfLines={2}>
                    {conversation.last_message || conversation.destination || 'Ready to continue'}
                  </Text>
                  <View style={styles.homeTripFoot}>
                    <Ionicons name="time-outline" size={14} color={colors.muted} />
                    <Text style={styles.homeTripDate}>
                      Updated {relativeDate(conversation.updated_at)}
                    </Text>
                  </View>
                </View>
              </Pressable>
            ))
          ) : (
            <Pressable
              onPress={() => composerRef.current?.focus()}
              style={({ pressed }) => [
                styles.emptyJourney,
                pressed && styles.pressed,
              ]}
            >
              <View style={styles.emptyJourneyIcon}>
                <Ionicons name="airplane" size={22} color={colors.primary} />
              </View>
              <View style={styles.emptyJourneyCopy}>
                <Text style={styles.emptyJourneyTitle}>Your next journey starts here</Text>
                <Text style={styles.emptyJourneyBody}>
                  Share a place, a budget, or simply the kind of break you need.
                </Text>
              </View>
              <Ionicons name="arrow-forward" size={18} color={colors.primary} />
            </Pressable>
          )}
        </View>

        {conversations.length ? (
          <>
            <SectionHeader title="Recent searches" />
            <View style={styles.recentCard}>
              {conversations.slice(0, 2).map((conversation, index) => (
                <Pressable
                  key={conversation.id}
                  onPress={() => onOpen(conversation.id)}
                  style={({ pressed }) => [
                    styles.recentRow,
                    index > 0 && styles.recentRowBorder,
                    pressed && styles.pressed,
                  ]}
                >
                  <View style={styles.recentIcon}>
                    <Ionicons
                      name={index % 2 ? 'location-outline' : 'time-outline'}
                      size={17}
                      color={colors.primary}
                    />
                  </View>
                  <View style={styles.recentCopy}>
                    <Text style={styles.recentTitle} numberOfLines={1}>
                      {conversation.title}
                    </Text>
                    <Text style={styles.recentMeta} numberOfLines={1}>
                      {conversation.destination || conversation.last_message || 'Saved plan'}
                    </Text>
                  </View>
                  <Text style={styles.recentDate}>{relativeDate(conversation.updated_at)}</Text>
                </Pressable>
              ))}
            </View>
          </>
        ) : null}

        <View style={styles.labRow}>
          <View style={styles.labIcon}>
            <Ionicons name="shield-checkmark-outline" size={19} color={colors.primary} />
          </View>
          <View style={styles.labCopy}>
            <Text style={styles.labTitle}>Resilience demo</Text>
            <Text style={styles.labBody}>Show how Safar recovers from one provider timeout.</Text>
          </View>
          <Switch
            value={resilienceDemo}
            onValueChange={setResilienceDemo}
            trackColor={{ false: colors.lineStrong, true: '#B9B1F2' }}
            thumbColor={resilienceDemo ? colors.primary : colors.surface}
          />
        </View>
      </ScrollView>
    </View>
  );
}

function TripWorkspace({
  snapshot,
  image,
  section,
  setSection,
  onBack,
  onSend,
  sending,
  error,
  composerText,
  setComposerText,
  composerRef,
  onApproval,
  approvalBusy,
  editingApproval,
}: {
  snapshot: ConversationSnapshot;
  image: ImageSource;
  section: TripSection;
  setSection: (section: TripSection) => void;
  onBack: () => void;
  onSend: (text: string) => Promise<void>;
  sending: boolean;
  error: string | null;
  composerText: string;
  setComposerText: (text: string) => void;
  composerRef: RefObject<TextInput | null>;
  onApproval: (decision: 'approve' | 'edit' | 'cancel') => void;
  approvalBusy: boolean;
  editingApproval: boolean;
}) {
  const run = snapshot.active_run;
  const dates = dateRange(run?.constraints);
  return (
    <View style={styles.workspace}>
      <View style={styles.tripHeader}>
        <Pressable
          onPress={onBack}
          accessibilityRole="button"
          accessibilityLabel="Back to home"
          style={({ pressed }) => [styles.headerCircle, pressed && styles.pressed]}
        >
          <Ionicons name="arrow-back" size={21} color={colors.ink} />
        </Pressable>
        <View style={styles.tripHeaderCopy}>
          <Text style={styles.tripHeaderTitle} numberOfLines={1}>
            {snapshot.conversation.title}
          </Text>
          <Text style={styles.tripHeaderMeta} numberOfLines={1}>
            {dates || snapshot.conversation.destination || 'Your travel plan'}
          </Text>
        </View>
        <View style={styles.headerActions}>
          <View style={styles.headerCircle}>
            <Ionicons name="heart-outline" size={21} color={colors.ink} />
          </View>
          <View style={styles.headerCircleSmall}>
            <Ionicons name="ellipsis-vertical" size={19} color={colors.ink} />
          </View>
        </View>
      </View>
      <View style={styles.segmented}>
        {tripSections.map((item) => {
          const selected = item.key === section;
          return (
            <Pressable
              key={item.key}
              onPress={() => setSection(item.key)}
              accessibilityRole="tab"
              accessibilityState={{ selected }}
              style={({ pressed }) => [
                styles.segment,
                selected && styles.segmentActive,
                pressed && styles.pressed,
              ]}
            >
              <Text style={[styles.segmentText, selected && styles.segmentTextActive]}>
                {item.label}
              </Text>
            </Pressable>
          );
        })}
      </View>

      {section === 'plan' ? (
        <ScrollView
          style={styles.workspaceScroll}
          contentContainerStyle={styles.workspaceContent}
          keyboardShouldPersistTaps="handled"
          showsVerticalScrollIndicator={false}
        >
          {run?.selected_package ? (
            <PlanOverview
              run={run}
              onApproval={onApproval}
              approvalBusy={approvalBusy}
            />
          ) : null}
          <Text style={styles.workspaceSectionTitle}>
            {run?.selected_package ? 'Planning trail' : 'Building your best route'}
          </Text>
          <View style={styles.messageStack}>
            {snapshot.messages.map((message) => (
              <MessageRenderer
                key={message.id}
                message={message}
                activeGraph={
                  message.run_id === run?.id ? run?.graph : undefined
                }
                onQuickReply={(reply) => void onSend(reply)}
                onApproval={onApproval}
                approvalBusy={approvalBusy}
              />
            ))}
          </View>
          <View style={styles.workspaceComposer}>
            {error ? <InlineError text={error} /> : null}
            <Composer
              ref={composerRef}
              value={composerText}
              onChangeText={setComposerText}
              onSend={onSend}
              busy={sending}
              variant="compact"
              placeholder={
                editingApproval
                  ? 'Describe the change…'
                  : run?.status === 'awaiting_input'
                    ? 'Answer Safar…'
                    : 'Ask Safar to change anything…'
              }
            />
          </View>
        </ScrollView>
      ) : null}
      {section === 'itinerary' ? (
        <ItineraryOverview
          run={run}
          image={image}
          onMap={() => setSection('map')}
        />
      ) : null}
      {section === 'map' ? <MapOverview run={run} /> : null}
      {section === 'details' ? (
        <DetailsOverview run={run} conversation={snapshot.conversation} />
      ) : null}
    </View>
  );
}

function PlanOverview({
  run,
  onApproval,
  approvalBusy,
}: {
  run: RunState;
  onApproval: (decision: 'approve' | 'edit' | 'cancel') => void;
  approvalBusy: boolean;
}) {
  const selected = run.selected_package;
  if (!selected) return null;
  const duration = run.itinerary?.days.length || run.constraints.duration_days || 0;
  const travellers = run.constraints.adults + run.constraints.children;
  const comfort = selected.hotel.rating || 0;
  const highlights = [
    ...run.constraints.preferences,
    `${selected.flight.stops ? `${selected.flight.stops}-stop` : 'Nonstop'} flights selected`,
    `${selected.hotel.name} · ${comfort ? `${comfort}★` : 'verified stay'}`,
    selected.remaining_budget != null
      ? `${formatCurrency(selected.remaining_budget)} kept as budget headroom`
      : 'Ranked for the best overall value',
  ].filter(Boolean).slice(0, 4);
  const included: Array<{
    label: string;
    meta: string;
    icon: keyof typeof Ionicons.glyphMap;
  }> = [
    { label: 'Flights', meta: 'Return', icon: 'airplane-outline' },
    {
      label: 'Hotels',
      meta: `${Math.max(1, duration - 1)} nights`,
      icon: 'bed-outline',
    },
    { label: 'Transport', meta: 'Local reserve', icon: 'car-outline' },
    {
      label: 'Activities',
      meta: `${run.itinerary?.days.reduce((sum, day) => sum + day.items.length, 0) || 0} planned`,
      icon: 'ticket-outline',
    },
  ];
  return (
    <View style={styles.planOverview}>
      <View style={styles.bestPlanCard}>
        <View style={styles.bestPlanHeading}>
          <View style={styles.sparkleMark}>
            <Ionicons name="sparkles" size={23} color={colors.primary} />
          </View>
          <View style={styles.bestPlanCopy}>
            <Text style={styles.bestPlanTitle}>Best plan found</Text>
            <Text style={styles.bestPlanBody}>
              Balanced around your budget, timing and preferences.
            </Text>
          </View>
        </View>
        <View style={styles.planStats}>
          <PlanStat
            value={formatCurrency(selected.total_price)}
            label="Trip total"
          />
          <PlanStat value={`${duration || '—'}`} label="Days" />
          <PlanStat value={`${travellers || '—'}`} label="Travellers" />
          <PlanStat value={comfort ? `${comfort}★` : '—'} label="Comfort" />
        </View>
      </View>
      <View style={styles.planMetricGrid}>
        <MetricCard
          title="Best time"
          value={compactDateRange(run.constraints)}
          meta={`${duration || 'Flexible'} day${duration === 1 ? '' : 's'}`}
        />
        <MetricCard
          title="Best budget"
          value={formatCurrency(selected.total_price)}
          meta={
            selected.remaining_budget != null
              ? `${formatCurrency(selected.remaining_budget)} spare`
              : 'No budget cap'
          }
        />
        <MetricCard
          title="Best comfort"
          value={comfort ? `${comfort} / 5` : 'Selected'}
          meta={selected.hotel.name}
        />
      </View>
      <View style={styles.planCard}>
        <Text style={styles.planCardTitle}>What’s included</Text>
        <View style={styles.includedGrid}>
          {included.map((item) => (
            <View key={item.label} style={styles.includedItem}>
              <View style={styles.includedIcon}>
                <Ionicons name={item.icon} size={21} color={colors.ink} />
              </View>
              <Text style={styles.includedLabel}>{item.label}</Text>
              <Text style={styles.includedMeta}>{item.meta}</Text>
            </View>
          ))}
        </View>
      </View>
      <View style={styles.planCard}>
        <Text style={styles.planCardTitle}>Trip highlights</Text>
        <View style={styles.highlightList}>
          {highlights.map((highlight) => (
            <View key={highlight} style={styles.highlightRow}>
              <Ionicons name="checkmark" size={16} color={colors.primary} />
              <Text style={styles.highlightText}>{highlight}</Text>
            </View>
          ))}
        </View>
      </View>
      {run.approval ? (
        <View style={styles.planActions}>
          <Pressable
            onPress={() => onApproval('edit')}
            disabled={approvalBusy}
            style={({ pressed }) => [
              styles.secondaryAction,
              pressed && styles.pressed,
            ]}
          >
            <Ionicons name="options-outline" size={18} color={colors.ink} />
            <Text style={styles.secondaryActionText}>Customize</Text>
          </Pressable>
          <Pressable
            onPress={() => onApproval('approve')}
            disabled={approvalBusy}
            style={({ pressed }) => [
              styles.primaryAction,
              pressed && styles.pressed,
            ]}
          >
            {approvalBusy ? (
              <ActivityIndicator size="small" color={colors.surface} />
            ) : (
              <Ionicons name="calendar-outline" size={18} color={colors.surface} />
            )}
            <Text style={styles.primaryActionText}>
              {approvalBusy ? 'Saving…' : 'Approve & save'}
            </Text>
          </Pressable>
        </View>
      ) : null}
    </View>
  );
}

function PlanStat({ value, label }: { value: string; label: string }) {
  return (
    <View style={styles.planStat}>
      <Text style={styles.planStatValue} numberOfLines={1} adjustsFontSizeToFit>
        {value}
      </Text>
      <Text style={styles.planStatLabel}>{label}</Text>
    </View>
  );
}

function MetricCard({
  title,
  value,
  meta,
}: {
  title: string;
  value: string;
  meta: string;
}) {
  return (
    <View style={styles.metricCard}>
      <Text style={styles.metricCardTitle}>{title}</Text>
      <Text style={styles.metricCardValue} numberOfLines={2}>
        {value}
      </Text>
      <Text style={styles.metricCardMeta} numberOfLines={2}>
        {meta}
      </Text>
    </View>
  );
}

function ItineraryOverview({
  run,
  image,
  onMap,
}: {
  run?: RunState;
  image: ImageSource;
  onMap: () => void;
}) {
  const [dayIndex, setDayIndex] = useState(0);
  const itinerary = run?.itinerary;
  if (!itinerary?.days.length) {
    return (
      <EmptyState
        icon="map-outline"
        title="Your itinerary is taking shape"
        body="Once Safar has compared the valid options, each day appears here as a clear timeline."
      />
    );
  }
  const safeIndex = Math.min(dayIndex, itinerary.days.length - 1);
  const day = itinerary.days[safeIndex] as ItineraryDay;
  const hotel = run?.selected_package?.hotel;
  return (
    <ScrollView
      style={styles.workspaceScroll}
      contentContainerStyle={styles.itineraryContent}
      showsVerticalScrollIndicator={false}
    >
      <ScrollView
        horizontal
        showsHorizontalScrollIndicator={false}
        contentContainerStyle={styles.dayTabs}
      >
        {itinerary.days.map((item, index) => {
          const selected = index === safeIndex;
          return (
            <Pressable
              key={item.date}
              onPress={() => setDayIndex(index)}
              style={({ pressed }) => [
                styles.dayTab,
                selected && styles.dayTabActive,
                pressed && styles.pressed,
              ]}
            >
              <Text style={[styles.dayTabTitle, selected && styles.dayTabTitleActive]}>
                Day {index + 1}
              </Text>
              <Text style={[styles.dayTabDate, selected && styles.dayTabDateActive]}>
                {shortDate(item.date)}
              </Text>
            </Pressable>
          );
        })}
      </ScrollView>
      <View style={styles.itineraryHero}>
        <Image source={image} style={StyleSheet.absoluteFill} contentFit="cover" />
        <View style={styles.itineraryHeroShade} />
        <View style={styles.itineraryHeroCopy}>
          <Text style={styles.itineraryHeroMeta}>
            Day {safeIndex + 1} · {formatDate(day.date)}
          </Text>
          <Text style={styles.itineraryHeroTitle}>{day.title}</Text>
        </View>
      </View>
      <View style={styles.timelineList}>
        {day.items.map((item, index) => (
          <View key={item.id} style={styles.timelineRow}>
            <Text style={styles.timelineTime}>{formatTime(item.start_at)}</Text>
            <View style={styles.timelineRail}>
              <View
                style={[
                  styles.timelineNode,
                  index % 2 ? styles.timelineNodeGreen : styles.timelineNodeBlue,
                ]}
              />
              {index < day.items.length - 1 ? (
                <View
                  style={[
                    styles.timelineStroke,
                    index % 2 ? styles.timelineStrokeGreen : styles.timelineStrokeBlue,
                  ]}
                />
              ) : null}
            </View>
            <View style={styles.timelineCopy}>
              <Text style={styles.timelineTitle}>{item.title}</Text>
              <Text style={styles.timelineBody} numberOfLines={2}>
                {item.location || item.description}
              </Text>
            </View>
            <Ionicons name="chevron-forward" size={16} color={colors.muted} />
          </View>
        ))}
      </View>
      {hotel ? (
        <View style={styles.staySection}>
          <Text style={styles.stayLabel}>Stay</Text>
          <View style={styles.stayCard}>
            {hotel.image_url ? (
              <Image
                source={{ uri: hotel.image_url }}
                style={styles.stayImage}
                contentFit="cover"
              />
            ) : (
              <View style={[styles.stayImage, styles.stayImageFallback]}>
                <Ionicons name="bed-outline" size={24} color={colors.primary} />
              </View>
            )}
            <View style={styles.stayCopy}>
              <Text style={styles.stayName} numberOfLines={1}>
                {hotel.name}
              </Text>
              <Text style={styles.stayLocation} numberOfLines={1}>
                {hotel.address}
              </Text>
              <Text style={styles.stayRating}>
                <Text style={styles.starText}>★</Text>{' '}
                {hotel.rating || 'New'}
                {hotel.review_count ? ` · ${hotel.review_count} reviews` : ''}
              </Text>
            </View>
            <View style={styles.stayAction}>
              <Ionicons name="call-outline" size={20} color={colors.ink} />
            </View>
          </View>
        </View>
      ) : null}
      <View style={styles.itineraryActions}>
        <Pressable
          onPress={onMap}
          style={({ pressed }) => [
            styles.mapAction,
            pressed && styles.pressed,
          ]}
        >
          <Ionicons name="map-outline" size={18} color={colors.primary} />
          <Text style={styles.mapActionText}>View on map</Text>
        </Pressable>
        <Pressable
          onPress={() => setDayIndex((safeIndex + 1) % itinerary.days.length)}
          style={({ pressed }) => [
            styles.nextAction,
            pressed && styles.pressed,
          ]}
        >
          <Text style={styles.nextActionText}>
            {safeIndex === itinerary.days.length - 1 ? 'First day' : 'Next day'}
          </Text>
          <Ionicons name="arrow-forward" size={19} color={colors.surface} />
        </Pressable>
      </View>
    </ScrollView>
  );
}

function MapOverview({ run }: { run?: RunState }) {
  if (!run?.itinerary) {
    return (
      <EmptyState
        icon="location-outline"
        title="No route to map yet"
        body="The route view becomes available as soon as your itinerary is ready."
      />
    );
  }
  return (
    <ScrollView
      style={styles.workspaceScroll}
      contentContainerStyle={styles.mapContent}
      showsVerticalScrollIndicator={false}
    >
      <View style={styles.mapIntro}>
        <View style={styles.mapIntroIcon}>
          <Ionicons name="navigate" size={22} color={colors.primary} />
        </View>
        <View style={styles.mapIntroCopy}>
          <Text style={styles.mapIntroTitle}>Your route, at a glance</Text>
          <Text style={styles.mapIntroBody}>
            Stops are ordered by day so transfers stay practical.
          </Text>
        </View>
      </View>
      <ItineraryMap itinerary={run.itinerary} />
    </ScrollView>
  );
}

function DetailsOverview({
  run,
  conversation,
}: {
  run?: RunState;
  conversation: Conversation;
}) {
  const constraints = run?.constraints;
  const details = constraintDetails(constraints);
  return (
    <ScrollView
      style={styles.workspaceScroll}
      contentContainerStyle={styles.detailsContent}
      showsVerticalScrollIndicator={false}
    >
      <View style={styles.detailsHero}>
        <Text style={styles.detailsEyebrow}>Trip details</Text>
        <Text style={styles.detailsTitle}>{conversation.title}</Text>
        <Text style={styles.detailsBody}>
          Everything Safar is using to compare your options.
        </Text>
      </View>
      <View style={styles.detailsCard}>
        {details.length ? (
          details.map((detail, index) => (
            <View
              key={detail.label}
              style={[styles.detailRow, index > 0 && styles.detailRowBorder]}
            >
              <View style={styles.detailIcon}>
                <Ionicons name={detail.icon} size={18} color={colors.primary} />
              </View>
              <View style={styles.detailCopy}>
                <Text style={styles.detailLabel}>{detail.label}</Text>
                <Text style={styles.detailValue}>{detail.value}</Text>
              </View>
            </View>
          ))
        ) : (
          <Text style={styles.detailsBody}>
            Send a planning request and your constraints will be collected here.
          </Text>
        )}
      </View>
      {run?.graph ? (
        <View style={styles.detailsCard}>
          <Text style={styles.planCardTitle}>Planning progress</Text>
          {run.graph.tasks.map((task) => (
            <View key={task.id} style={styles.taskDetail}>
              <View
                style={[
                  styles.taskDot,
                  task.status === 'completed' && styles.taskDotComplete,
                  task.status === 'failed' && styles.taskDotFailed,
                ]}
              />
              <View style={styles.taskDetailCopy}>
                <Text style={styles.taskDetailTitle}>{task.title}</Text>
                <Text style={styles.taskDetailStatus}>
                  {task.status.replace('_', ' ')}
                  {task.provider ? ` · ${task.provider}` : ''}
                </Text>
              </View>
            </View>
          ))}
        </View>
      ) : null}
    </ScrollView>
  );
}

function TripsScreen({
  conversations,
  activeId,
  onOpen,
  onNewTrip,
}: {
  conversations: Conversation[];
  activeId: string | null;
  onOpen: (id: string) => void;
  onNewTrip: () => void;
}) {
  const images = tripImageAssignments(
    conversations.map((conversation) => conversation.id),
  );
  return (
    <View style={styles.standardScreen}>
      <ScreenHeader
        eyebrow="Saved journeys"
        title="Your trips"
        trailing={
          <Pressable
            onPress={onNewTrip}
            accessibilityRole="button"
            accessibilityLabel="Plan a new trip"
            style={({ pressed }) => [styles.headerCircle, pressed && styles.pressed]}
          >
            <Ionicons name="add" size={22} color={colors.ink} />
          </Pressable>
        }
      />
      <ScrollView
        contentContainerStyle={styles.tabContent}
        showsVerticalScrollIndicator={false}
      >
        <LinearGradient colors={gradients.navy} style={styles.tripsHero}>
          <Text style={styles.tripsHeroEyebrow}>Your travel archive</Text>
          <Text style={styles.tripsHeroNumber}>{conversations.length}</Text>
          <Text style={styles.tripsHeroBody}>
            Every decision, option and itinerary stays attached to its journey.
          </Text>
          <Pressable
            onPress={onNewTrip}
            style={({ pressed }) => [
              styles.tripsHeroAction,
              pressed && styles.pressed,
            ]}
          >
            <Ionicons name="sparkles" size={17} color={colors.primary} />
            <Text style={styles.tripsHeroActionText}>Plan something new</Text>
          </Pressable>
        </LinearGradient>
        <View style={styles.tripList}>
          {conversations.map((conversation) => (
            <Pressable
              key={conversation.id}
              onPress={() => onOpen(conversation.id)}
              style={({ pressed }) => [
                styles.tripCard,
                conversation.id === activeId && styles.tripCardActive,
                pressed && styles.pressed,
              ]}
            >
              <Image
                source={
                  images.get(conversation.id) ??
                  tripImageForKey(conversation.id)
                }
                style={styles.tripCardImage}
                contentFit="cover"
              />
              <View style={styles.tripCardCopy}>
                <Text style={styles.tripCardTitle} numberOfLines={1}>
                  {conversation.title}
                </Text>
                <Text style={styles.tripCardBody} numberOfLines={2}>
                  {conversation.last_message || conversation.destination || 'Ready to continue'}
                </Text>
                <Text style={styles.tripCardDate}>
                  {relativeDate(conversation.updated_at)}
                </Text>
              </View>
              <Ionicons name="chevron-forward" size={18} color={colors.muted} />
            </Pressable>
          ))}
          {!conversations.length ? (
            <EmptyState
              icon="briefcase-outline"
              title="No saved trips yet"
              body="Your first plan will appear here with its full decision trail."
              compact
            />
          ) : null}
        </View>
      </ScrollView>
    </View>
  );
}

function ActivityScreen({
  events,
  messages,
  tripTitle,
}: {
  events: AgentEvent[];
  messages: ChatMessage[];
  tripTitle?: string;
}) {
  const operations = useMemo(() => {
    if (events.length) {
      return events
        .slice()
        .reverse()
        .map((event) => ({
          id: String(event.id),
          createdAt: event.created_at,
          status: event.status,
          summary: event.summary,
          reason: event.reason || event.type.replaceAll('_', ' '),
        }));
    }
    return messages
      .filter((message) => ['operation', 'error', 'calendar'].includes(message.kind))
      .slice()
      .reverse()
      .map((message) => {
        const event = message.payload.event as OperationEvent | undefined;
        return {
          id: message.id,
          createdAt: message.created_at,
          status:
            event?.status || (message.kind === 'error' ? 'failed' : 'completed'),
          summary: event?.summary || message.text,
          reason: event?.reason || message.kind.replaceAll('_', ' '),
        };
      });
  }, [events, messages]);
  return (
    <View style={styles.standardScreen}>
      <ScreenHeader eyebrow="Live decisions" title="Activity" />
      <ScrollView
        contentContainerStyle={styles.tabContent}
        showsVerticalScrollIndicator={false}
      >
        <View style={styles.activityHero}>
          <View style={styles.activityHeroMark}>
            <Ionicons name="pulse" size={22} color={colors.primary} />
          </View>
          <Text style={styles.activityHeroEyebrow}>Transparent by default</Text>
          <Text style={styles.activityHeroTitle}>What Safar did</Text>
          <Text style={styles.activityHeroBody}>
            {tripTitle
              ? `The live action log for ${tripTitle}.`
              : 'Open a trip to see each provider call, recovery and calendar action.'}
          </Text>
        </View>
        {operations.length ? (
          <View style={styles.activityList}>
            {operations.map((message, index) => {
              const status = message.status;
              return (
                <View key={message.id} style={styles.activityRow}>
                  <View style={styles.activityTimeColumn}>
                    <Text style={styles.activityTime}>
                      {relativeDate(message.createdAt)}
                    </Text>
                  </View>
                  <View style={styles.activityRail}>
                    <View
                      style={[
                        styles.activityNode,
                        status === 'retrying' && styles.activityNodeRetry,
                        status === 'failed' && styles.activityNodeFailed,
                      ]}
                    />
                    {index < operations.length - 1 ? (
                      <View style={styles.activityLine} />
                    ) : null}
                  </View>
                  <View style={styles.activityCopy}>
                    <Text style={styles.activitySummary}>
                      {message.summary}
                    </Text>
                    <Text style={styles.activityReason}>
                      {message.reason || status.replace('_', ' ')}
                    </Text>
                  </View>
                </View>
              );
            })}
          </View>
        ) : (
          <EmptyState
            icon="pulse-outline"
            title="No execution yet"
            body="Start or open a trip and its live action log will appear here."
            compact
          />
        )}
      </ScrollView>
    </View>
  );
}

function ProfileScreen({
  accessToken,
  name,
  email,
  avatar,
  resilienceDemo,
  setResilienceDemo,
  connectCalendar,
  signOut,
}: {
  accessToken: string;
  name: string;
  email: string;
  avatar?: string;
  resilienceDemo: boolean;
  setResilienceDemo: (value: boolean) => void;
  connectCalendar: () => Promise<boolean>;
  signOut: () => Promise<void>;
}) {
  const [calendarConnected, setCalendarConnected] = useState(false);
  const [calendarBusy, setCalendarBusy] = useState(false);
  useEffect(() => {
    void api
      .calendarStatus(accessToken)
      .then((status) => setCalendarConnected(status.connected))
      .catch(() => setCalendarConnected(false));
  }, [accessToken]);

  const toggleCalendar = async () => {
    setCalendarBusy(true);
    try {
      if (calendarConnected) {
        await api.disconnectCalendar(accessToken);
        setCalendarConnected(false);
      } else {
        setCalendarConnected(await connectCalendar());
      }
    } catch (calendarError) {
      Alert.alert('Calendar', messageForError(calendarError));
    } finally {
      setCalendarBusy(false);
    }
  };

  return (
    <View style={styles.standardScreen}>
      <ScreenHeader eyebrow="Safar account" title="Your account" />
      <ScrollView
        contentContainerStyle={styles.tabContent}
        showsVerticalScrollIndicator={false}
      >
        <LinearGradient colors={gradients.navy} style={styles.profileCard}>
          {avatar ? (
            <Image source={{ uri: avatar }} style={styles.profileAvatar} />
          ) : (
            <View style={styles.profileBrand}>
              <BrandMark size={62} />
            </View>
          )}
          <Text style={styles.profileName}>{name}</Text>
          <Text style={styles.profileEmail}>{email}</Text>
          <View style={styles.googleOnly}>
            <Text style={styles.googleLetter}>G</Text>
            <Text style={styles.googleOnlyText}>Signed in with Google</Text>
          </View>
        </LinearGradient>
        <Text style={styles.settingsLabel}>Connections</Text>
        <View style={styles.settingsGroup}>
          <SettingRow
            icon="calendar-outline"
            title="Google Calendar"
            subtitle={
              calendarConnected
                ? 'Connected · every write still needs your approval'
                : 'Connect when you are ready to save an itinerary'
            }
            action={
              calendarBusy ? (
                <ActivityIndicator size="small" color={colors.primary} />
              ) : (
                <Switch
                  value={calendarConnected}
                  onValueChange={() => void toggleCalendar()}
                  trackColor={{ false: colors.lineStrong, true: '#B9B1F2' }}
                  thumbColor={calendarConnected ? colors.primary : colors.surface}
                />
              )
            }
          />
        </View>
        <Text style={styles.settingsLabel}>Planning lab</Text>
        <View style={styles.settingsGroup}>
          <SettingRow
            icon="shield-checkmark-outline"
            title="Resilience demo"
            subtitle="Labels and recovers from one injected provider timeout"
            action={
              <Switch
                value={resilienceDemo}
                onValueChange={setResilienceDemo}
                trackColor={{ false: colors.lineStrong, true: '#B9B1F2' }}
                thumbColor={resilienceDemo ? colors.primary : colors.surface}
              />
            }
          />
        </View>
        <Pressable
          onPress={() => void signOut()}
          style={({ pressed }) => [styles.signOut, pressed && styles.pressed]}
        >
          <Ionicons name="log-out-outline" size={19} color={colors.coral} />
          <Text style={styles.signOutText}>Sign out</Text>
        </Pressable>
        <Text style={styles.authNote}>
          Safar uses Google sign-in only. Calendar access remains separate and is
          requested only when you choose to connect it.
        </Text>
      </ScrollView>
    </View>
  );
}

function ScreenHeader({
  eyebrow,
  title,
  trailing,
}: {
  eyebrow: string;
  title: string;
  trailing?: ReactNode;
}) {
  return (
    <View style={styles.screenHeader}>
      <View style={styles.screenHeaderIdentity}>
        <BrandMark size={42} />
        <View>
          <Text style={styles.screenHeaderEyebrow}>{eyebrow}</Text>
          <Text style={styles.screenHeaderTitle}>{title}</Text>
        </View>
      </View>
      {trailing || <View style={styles.headerCircleSmall} />}
    </View>
  );
}

function SectionHeader({
  title,
  action,
  onPress,
}: {
  title: string;
  action?: string;
  onPress?: () => void;
}) {
  return (
    <View style={styles.sectionHeader}>
      <Text style={styles.sectionHeaderTitle}>{title}</Text>
      {action && onPress ? (
        <Pressable onPress={onPress} hitSlop={10}>
          <Text style={styles.sectionHeaderAction}>{action}</Text>
        </Pressable>
      ) : null}
    </View>
  );
}

function SettingRow({
  icon,
  title,
  subtitle,
  action,
}: {
  icon: keyof typeof Ionicons.glyphMap;
  title: string;
  subtitle: string;
  action: ReactNode;
}) {
  return (
    <View style={styles.settingRow}>
      <View style={styles.settingIcon}>
        <Ionicons name={icon} size={20} color={colors.primary} />
      </View>
      <View style={styles.settingCopy}>
        <Text style={styles.settingTitle}>{title}</Text>
        <Text style={styles.settingSubtitle}>{subtitle}</Text>
      </View>
      {action}
    </View>
  );
}

function EmptyState({
  icon,
  title,
  body,
  compact = false,
}: {
  icon: keyof typeof Ionicons.glyphMap;
  title: string;
  body: string;
  compact?: boolean;
}) {
  return (
    <View style={[styles.emptyState, compact && styles.emptyStateCompact]}>
      <View style={styles.emptyStateIcon}>
        <Ionicons name={icon} size={25} color={colors.primary} />
      </View>
      <Text style={styles.emptyStateTitle}>{title}</Text>
      <Text style={styles.emptyStateBody}>{body}</Text>
    </View>
  );
}

function InlineError({ text }: { text: string }) {
  return (
    <View style={styles.inlineError}>
      <Ionicons name="alert-circle" size={16} color={colors.coral} />
      <Text style={styles.inlineErrorText}>{text}</Text>
    </View>
  );
}

function messageForError(error: unknown): string {
  if (error instanceof ApiError) {
    if (
      typeof error.detail === 'object' &&
      error.detail &&
      'message' in error.detail
    ) {
      return String((error.detail as { message: unknown }).message);
    }
    return error.message;
  }
  return error instanceof Error ? error.message : 'Something went wrong.';
}

function dateRange(constraints?: TravelConstraints) {
  if (!constraints?.start_date && !constraints?.end_date) return '';
  if (constraints.start_date && constraints.end_date) {
    return `${formatDate(constraints.start_date)} – ${formatDate(constraints.end_date)}`;
  }
  return formatDate(constraints.start_date || constraints.end_date || '');
}

function compactDateRange(constraints: TravelConstraints) {
  if (constraints.start_date && constraints.end_date) {
    return `${shortDate(constraints.start_date)} – ${shortDate(constraints.end_date)}`;
  }
  return constraints.start_date ? shortDate(constraints.start_date) : 'Flexible';
}

function shortDate(value: string) {
  const date = new Date(`${value.slice(0, 10)}T12:00:00`);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat('en-IN', {
    month: 'short',
    day: 'numeric',
  }).format(date);
}

function constraintDetails(constraints?: TravelConstraints): Array<{
  label: string;
  value: string;
  icon: keyof typeof Ionicons.glyphMap;
}> {
  if (!constraints) return [];
  return [
    constraints.origin || constraints.destination
      ? {
          label: 'Route',
          value: `${constraints.origin || 'Flexible'} → ${constraints.destination || 'Flexible'}`,
          icon: 'airplane-outline' as const,
        }
      : null,
    constraints.start_date || constraints.end_date
      ? {
          label: 'Dates',
          value: dateRange(constraints),
          icon: 'calendar-outline' as const,
        }
      : null,
    constraints.budget
      ? {
          label: 'Budget',
          value: `Up to ${formatCurrency(constraints.budget)}`,
          icon: 'wallet-outline' as const,
        }
      : null,
    {
      label: 'Travellers',
      value: `${constraints.adults} adult${constraints.adults === 1 ? '' : 's'}${
        constraints.children
          ? ` · ${constraints.children} child${constraints.children === 1 ? '' : 'ren'}`
          : ''
      }`,
      icon: 'people-outline' as const,
    },
    constraints.preferences.length
      ? {
          label: 'Preferences',
          value: constraints.preferences.join(' · '),
          icon: 'heart-outline' as const,
        }
      : null,
  ].filter(Boolean) as Array<{
    label: string;
    value: string;
    icon: keyof typeof Ionicons.glyphMap;
  }>;
}

const styles = StyleSheet.create({
  webCanvas: {
    flex: 1,
    alignItems: 'center',
    backgroundColor: '#EEECF7',
  },
  safe: {
    flex: 1,
    width: '100%',
    maxWidth: layout.maxWidth,
    alignSelf: 'center',
    overflow: 'hidden',
    backgroundColor: colors.canvas,
    ...Platform.select({
      web: {
        minHeight: '100vh' as unknown as number,
        boxShadow: '0 8px 30px rgba(17, 24, 76, 0.10)',
      },
      default: {},
    }),
  },
  safeDark: { backgroundColor: colors.navy },
  content: { flex: 1, backgroundColor: colors.canvas },
  dockWrap: {
    position: 'absolute',
    left: 14,
    right: 14,
    zIndex: 40,
  },
  pressed: { opacity: 0.76, transform: [{ scale: 0.985 }] },
  homeScreen: { flex: 1, backgroundColor: colors.canvas },
  homeHeader: {
    height: 220,
    paddingHorizontal: layout.gutter,
    paddingTop: 14,
  },
  homeTopRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  headerCircleDark: {
    width: 42,
    height: 42,
    borderRadius: 21,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: 'rgba(255,255,255,0.06)',
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.09)',
  },
  notificationDot: {
    position: 'absolute',
    right: 4,
    top: 2,
    width: 9,
    height: 9,
    borderRadius: 5,
    backgroundColor: colors.coral,
    borderWidth: 2,
    borderColor: colors.navy,
  },
  greeting: { alignItems: 'center', flex: 1, paddingHorizontal: 10 },
  greetingTitle: { ...type.label, color: colors.surface },
  greetingBody: { ...type.body, color: colors.whiteMuted, marginTop: 2 },
  homeScroll: { flex: 1, marginTop: -80 },
  homeScrollContent: {
    paddingHorizontal: layout.gutter,
    paddingBottom: 116,
    gap: 14,
  },
  homePrompt: { gap: 7, ...floatingShadow },
  quickGrid: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    paddingTop: 3,
  },
  quickAction: { width: '23%', alignItems: 'center', gap: 7 },
  quickIcon: {
    width: '100%',
    height: 54,
    borderRadius: 14,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.line,
    ...shadow,
  },
  quickLabel: { ...type.caption, color: colors.ink, textAlign: 'center' },
  sectionHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    marginTop: 2,
  },
  sectionHeaderTitle: { ...type.section, color: colors.ink },
  sectionHeaderAction: { ...type.caption, color: colors.primary },
  homeTripList: { gap: 9 },
  homeTripCard: {
    minHeight: 99,
    borderRadius: 16,
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.line,
    overflow: 'hidden',
    flexDirection: 'row',
    alignItems: 'stretch',
    ...shadow,
  },
  homeTripCardActive: { borderColor: '#C8C1F2' },
  homeTripImage: { width: 104, backgroundColor: colors.surfaceTint },
  homeTripCopy: { flex: 1, padding: 12, gap: 5, minWidth: 0 },
  tripTitleRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
  },
  homeTripTitle: { ...type.label, color: colors.ink, flex: 1 },
  statusPill: {
    paddingHorizontal: 7,
    paddingVertical: 3,
    borderRadius: radius.pill,
    backgroundColor: colors.greenSoft,
  },
  statusPillActive: { backgroundColor: colors.infoSoft },
  statusText: { fontFamily: fonts.medium, fontSize: 9, color: colors.green },
  statusTextActive: { color: colors.info },
  homeTripMeta: { ...type.caption, color: colors.muted, flex: 1 },
  homeTripFoot: { flexDirection: 'row', alignItems: 'center', gap: 5 },
  homeTripDate: { ...type.caption, color: colors.muted },
  emptyJourney: {
    minHeight: 90,
    borderRadius: 16,
    padding: 13,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.line,
  },
  emptyJourneyIcon: {
    width: 48,
    height: 48,
    borderRadius: 15,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: colors.primarySoft,
  },
  emptyJourneyCopy: { flex: 1 },
  emptyJourneyTitle: { ...type.label, color: colors.ink },
  emptyJourneyBody: { ...type.caption, color: colors.muted, marginTop: 3 },
  recentCard: {
    borderRadius: 16,
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.line,
    overflow: 'hidden',
  },
  recentRow: {
    minHeight: 58,
    paddingHorizontal: 12,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
  },
  recentRowBorder: { borderTopWidth: 1, borderTopColor: colors.line },
  recentIcon: {
    width: 32,
    height: 32,
    borderRadius: 11,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: colors.primarySoft,
  },
  recentCopy: { flex: 1, minWidth: 0 },
  recentTitle: { ...type.caption, color: colors.ink },
  recentMeta: { fontFamily: fonts.regular, fontSize: 10, color: colors.muted },
  recentDate: { fontFamily: fonts.regular, fontSize: 9, color: colors.muted },
  labRow: {
    minHeight: 68,
    paddingHorizontal: 12,
    borderRadius: 16,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    backgroundColor: colors.surfaceTint,
  },
  labIcon: {
    width: 40,
    height: 40,
    borderRadius: 13,
    backgroundColor: colors.primarySoft,
    alignItems: 'center',
    justifyContent: 'center',
  },
  labCopy: { flex: 1 },
  labTitle: { ...type.label, color: colors.ink },
  labBody: { ...type.caption, color: colors.muted, marginTop: 2 },
  workspace: { flex: 1, backgroundColor: colors.canvas },
  tripHeader: {
    minHeight: 62,
    paddingHorizontal: 12,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 9,
    backgroundColor: colors.surface,
  },
  headerCircle: {
    width: 40,
    height: 40,
    borderRadius: 20,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.line,
  },
  headerCircleSmall: {
    width: 40,
    height: 40,
    borderRadius: 20,
    alignItems: 'center',
    justifyContent: 'center',
  },
  tripHeaderCopy: { flex: 1, alignItems: 'center', minWidth: 0 },
  tripHeaderTitle: { ...type.label, color: colors.ink },
  tripHeaderMeta: { ...type.caption, color: colors.muted, marginTop: 1 },
  headerActions: { flexDirection: 'row', alignItems: 'center', gap: 2 },
  segmented: {
    height: 42,
    marginHorizontal: 14,
    marginVertical: 8,
    padding: 3,
    borderRadius: 12,
    flexDirection: 'row',
    backgroundColor: colors.surfaceTint,
  },
  segment: {
    flex: 1,
    borderRadius: 10,
    alignItems: 'center',
    justifyContent: 'center',
  },
  segmentActive: { backgroundColor: colors.primary },
  segmentText: { ...type.caption, color: colors.inkSoft },
  segmentTextActive: { color: colors.surface },
  workspaceScroll: { flex: 1 },
  workspaceContent: {
    paddingHorizontal: 14,
    paddingTop: 2,
    paddingBottom: 118,
  },
  workspaceSectionTitle: {
    ...type.section,
    color: colors.ink,
    marginTop: 17,
    marginBottom: 6,
  },
  messageStack: { gap: 2 },
  workspaceComposer: { marginTop: 16, gap: 7 },
  planOverview: { gap: 10 },
  bestPlanCard: {
    borderRadius: 16,
    padding: 15,
    gap: 15,
    backgroundColor: colors.surfaceViolet,
    borderWidth: 1,
    borderColor: '#DED8F5',
    ...shadow,
  },
  bestPlanHeading: { flexDirection: 'row', alignItems: 'center', gap: 10 },
  sparkleMark: {
    width: 38,
    height: 38,
    borderRadius: 13,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: colors.surface,
  },
  bestPlanCopy: { flex: 1 },
  bestPlanTitle: { ...type.label, color: colors.ink },
  bestPlanBody: { ...type.caption, color: colors.muted, marginTop: 1 },
  planStats: { flexDirection: 'row' },
  planStat: {
    flex: 1,
    minWidth: 0,
    alignItems: 'center',
    paddingHorizontal: 4,
    borderRightWidth: 1,
    borderRightColor: '#D9D4EC',
  },
  planStatValue: {
    fontFamily: fonts.bold,
    fontSize: 15,
    lineHeight: 20,
    color: colors.ink,
  },
  planStatLabel: {
    fontFamily: fonts.regular,
    fontSize: 8.5,
    lineHeight: 13,
    color: colors.muted,
    textAlign: 'center',
  },
  planMetricGrid: { flexDirection: 'row', gap: 7 },
  metricCard: {
    flex: 1,
    minHeight: 100,
    borderRadius: 14,
    padding: 10,
    alignItems: 'center',
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.line,
    ...shadow,
  },
  metricCardTitle: { ...type.caption, color: colors.ink, textAlign: 'center' },
  metricCardValue: {
    fontFamily: fonts.bold,
    fontSize: 12,
    lineHeight: 17,
    color: colors.ink,
    textAlign: 'center',
    marginTop: 8,
  },
  metricCardMeta: {
    fontFamily: fonts.regular,
    fontSize: 8.5,
    lineHeight: 12,
    color: colors.muted,
    textAlign: 'center',
    marginTop: 5,
  },
  planCard: {
    borderRadius: 16,
    padding: 14,
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.line,
  },
  planCardTitle: { ...type.label, color: colors.ink },
  includedGrid: { flexDirection: 'row', marginTop: 12 },
  includedItem: { flex: 1, alignItems: 'center', minWidth: 0 },
  includedIcon: {
    width: 42,
    height: 42,
    borderRadius: 13,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: colors.surfaceTint,
  },
  includedLabel: {
    fontFamily: fonts.semiBold,
    fontSize: 9.5,
    color: colors.ink,
    marginTop: 6,
  },
  includedMeta: {
    fontFamily: fonts.regular,
    fontSize: 7.5,
    lineHeight: 11,
    color: colors.muted,
    textAlign: 'center',
  },
  highlightList: { gap: 8, marginTop: 11 },
  highlightRow: { flexDirection: 'row', alignItems: 'flex-start', gap: 7 },
  highlightText: { ...type.caption, color: colors.inkSoft, flex: 1 },
  planActions: { flexDirection: 'row', gap: 9 },
  secondaryAction: {
    minHeight: 50,
    paddingHorizontal: 14,
    borderRadius: 14,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 7,
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.lineStrong,
  },
  secondaryActionText: { ...type.label, color: colors.ink },
  primaryAction: {
    flex: 1,
    minHeight: 50,
    borderRadius: 14,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 8,
    backgroundColor: colors.primary,
  },
  primaryActionText: { ...type.label, color: colors.surface },
  itineraryContent: {
    paddingHorizontal: 14,
    paddingTop: 3,
    paddingBottom: 118,
    gap: 14,
  },
  dayTabs: { gap: 7, paddingRight: 12 },
  dayTab: {
    minWidth: 65,
    height: 50,
    paddingHorizontal: 9,
    borderRadius: 12,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.line,
  },
  dayTabActive: { backgroundColor: colors.primary, borderColor: colors.primary },
  dayTabTitle: { ...type.caption, color: colors.ink },
  dayTabTitleActive: { color: colors.surface },
  dayTabDate: { fontFamily: fonts.regular, fontSize: 8.5, color: colors.muted },
  dayTabDateActive: { color: '#DAD6F8' },
  itineraryHero: {
    height: 198,
    borderRadius: 19,
    overflow: 'hidden',
    justifyContent: 'flex-end',
    backgroundColor: colors.surfaceTint,
  },
  itineraryHeroShade: {
    position: 'absolute',
    left: 0,
    right: 0,
    bottom: 0,
    top: '42%',
    backgroundColor: 'rgba(8, 12, 38, 0.43)',
  },
  itineraryHeroCopy: { padding: 14 },
  itineraryHeroMeta: { ...type.caption, color: '#E5E3F5' },
  itineraryHeroTitle: {
    fontFamily: fonts.bold,
    fontSize: 21,
    lineHeight: 27,
    color: colors.surface,
    marginTop: 2,
  },
  timelineList: { paddingTop: 3 },
  timelineRow: {
    minHeight: 58,
    flexDirection: 'row',
    alignItems: 'flex-start',
  },
  timelineTime: {
    width: 58,
    fontFamily: fonts.medium,
    fontSize: 9,
    lineHeight: 15,
    color: colors.muted,
    paddingTop: 1,
  },
  timelineRail: { width: 22, alignItems: 'center', alignSelf: 'stretch' },
  timelineNode: {
    width: 10,
    height: 10,
    borderRadius: 5,
    backgroundColor: colors.surface,
    borderWidth: 2,
    zIndex: 2,
  },
  timelineNodeBlue: { borderColor: colors.info },
  timelineNodeGreen: { borderColor: colors.green },
  timelineStroke: { width: 1.5, flex: 1, marginVertical: 1 },
  timelineStrokeBlue: { backgroundColor: '#AFC9EE' },
  timelineStrokeGreen: { backgroundColor: '#A8D8C0' },
  timelineCopy: { flex: 1, paddingBottom: 14, minWidth: 0 },
  timelineTitle: { ...type.caption, color: colors.ink },
  timelineBody: {
    fontFamily: fonts.regular,
    fontSize: 9,
    lineHeight: 14,
    color: colors.muted,
    marginTop: 2,
  },
  staySection: {
    borderRadius: 16,
    padding: 11,
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.line,
  },
  stayLabel: { ...type.caption, color: colors.ink, marginBottom: 8 },
  stayCard: { flexDirection: 'row', alignItems: 'center', gap: 10 },
  stayImage: {
    width: 92,
    height: 64,
    borderRadius: 12,
    backgroundColor: colors.surfaceTint,
  },
  stayImageFallback: {
    alignItems: 'center',
    justifyContent: 'center',
  },
  stayCopy: { flex: 1, minWidth: 0 },
  stayName: { ...type.label, color: colors.ink },
  stayLocation: { ...type.caption, color: colors.muted, marginTop: 1 },
  stayRating: {
    fontFamily: fonts.medium,
    fontSize: 9.5,
    color: colors.muted,
    marginTop: 3,
  },
  starText: { color: colors.amber },
  stayAction: {
    width: 40,
    height: 40,
    borderRadius: 20,
    alignItems: 'center',
    justifyContent: 'center',
    borderWidth: 1,
    borderColor: colors.line,
  },
  itineraryActions: { flexDirection: 'row', gap: 9 },
  mapAction: {
    minHeight: 50,
    paddingHorizontal: 15,
    borderRadius: 14,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 7,
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.line,
  },
  mapActionText: { ...type.label, color: colors.ink },
  nextAction: {
    flex: 1,
    minHeight: 50,
    borderRadius: 14,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 8,
    backgroundColor: colors.primary,
  },
  nextActionText: { ...type.label, color: colors.surface },
  mapContent: {
    paddingHorizontal: 14,
    paddingTop: 4,
    paddingBottom: 118,
    gap: 12,
  },
  mapIntro: {
    padding: 14,
    borderRadius: 16,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 11,
    backgroundColor: colors.surfaceViolet,
  },
  mapIntroIcon: {
    width: 42,
    height: 42,
    borderRadius: 14,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: colors.surface,
  },
  mapIntroCopy: { flex: 1 },
  mapIntroTitle: { ...type.label, color: colors.ink },
  mapIntroBody: { ...type.caption, color: colors.muted, marginTop: 2 },
  detailsContent: {
    paddingHorizontal: 14,
    paddingTop: 4,
    paddingBottom: 118,
    gap: 11,
  },
  detailsHero: {
    minHeight: 160,
    padding: 18,
    borderRadius: 18,
    justifyContent: 'flex-end',
    backgroundColor: colors.surfaceViolet,
  },
  detailsEyebrow: {
    ...type.caption,
    color: colors.primary,
    textTransform: 'uppercase',
    letterSpacing: 0.7,
  },
  detailsTitle: { ...type.title, color: colors.ink, marginTop: 4 },
  detailsBody: { ...type.body, color: colors.muted, marginTop: 5 },
  detailsCard: {
    padding: 14,
    borderRadius: 16,
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.line,
  },
  detailRow: {
    minHeight: 60,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 11,
  },
  detailRowBorder: { borderTopWidth: 1, borderTopColor: colors.line },
  detailIcon: {
    width: 38,
    height: 38,
    borderRadius: 12,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: colors.primarySoft,
  },
  detailCopy: { flex: 1 },
  detailLabel: { ...type.caption, color: colors.muted },
  detailValue: { ...type.label, color: colors.ink, marginTop: 1 },
  taskDetail: {
    minHeight: 52,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
  },
  taskDot: {
    width: 10,
    height: 10,
    borderRadius: 5,
    backgroundColor: colors.lineStrong,
  },
  taskDotComplete: { backgroundColor: colors.green },
  taskDotFailed: { backgroundColor: colors.coral },
  taskDetailCopy: { flex: 1 },
  taskDetailTitle: { ...type.caption, color: colors.ink },
  taskDetailStatus: {
    fontFamily: fonts.regular,
    fontSize: 9,
    color: colors.muted,
    textTransform: 'capitalize',
  },
  standardScreen: { flex: 1, backgroundColor: colors.canvas },
  screenHeader: {
    minHeight: 68,
    paddingHorizontal: 14,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    backgroundColor: colors.surface,
    borderBottomWidth: 1,
    borderBottomColor: colors.line,
  },
  screenHeaderIdentity: { flexDirection: 'row', alignItems: 'center', gap: 10 },
  screenHeaderEyebrow: { ...type.caption, color: colors.muted },
  screenHeaderTitle: { ...type.section, color: colors.ink },
  tabContent: {
    padding: 14,
    paddingBottom: 118,
    gap: 13,
  },
  tripsHero: {
    minHeight: 190,
    padding: 18,
    borderRadius: 20,
    justifyContent: 'flex-end',
    overflow: 'hidden',
  },
  tripsHeroEyebrow: {
    ...type.caption,
    color: colors.whiteMuted,
    textTransform: 'uppercase',
    letterSpacing: 0.7,
  },
  tripsHeroNumber: {
    fontFamily: fonts.extraBold,
    fontSize: 46,
    lineHeight: 51,
    color: colors.surface,
  },
  tripsHeroBody: { ...type.body, color: colors.whiteMuted, maxWidth: 310 },
  tripsHeroAction: {
    alignSelf: 'flex-start',
    minHeight: 38,
    marginTop: 12,
    paddingHorizontal: 12,
    borderRadius: 12,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 7,
    backgroundColor: colors.surface,
  },
  tripsHeroActionText: { ...type.caption, color: colors.ink },
  tripList: { gap: 9 },
  tripCard: {
    minHeight: 96,
    padding: 10,
    borderRadius: 16,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 11,
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.line,
    ...shadow,
  },
  tripCardActive: { borderColor: colors.primary },
  tripCardImage: {
    width: 78,
    height: 72,
    borderRadius: 13,
    backgroundColor: colors.surfaceTint,
  },
  tripCardCopy: { flex: 1, minWidth: 0 },
  tripCardTitle: { ...type.label, color: colors.ink },
  tripCardBody: { ...type.caption, color: colors.muted, marginTop: 3 },
  tripCardDate: { ...type.caption, color: colors.primary, marginTop: 4 },
  activityHero: {
    minHeight: 190,
    padding: 18,
    borderRadius: 20,
    justifyContent: 'flex-end',
    backgroundColor: colors.surfaceViolet,
    borderWidth: 1,
    borderColor: '#DED8F5',
  },
  activityHeroMark: {
    position: 'absolute',
    left: 18,
    top: 18,
    width: 46,
    height: 46,
    borderRadius: 15,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: colors.surface,
  },
  activityHeroEyebrow: {
    ...type.caption,
    color: colors.primary,
    textTransform: 'uppercase',
    letterSpacing: 0.7,
  },
  activityHeroTitle: { ...type.title, color: colors.ink, marginTop: 4 },
  activityHeroBody: { ...type.body, color: colors.muted, marginTop: 5 },
  activityList: {
    padding: 13,
    borderRadius: 17,
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.line,
  },
  activityRow: { minHeight: 65, flexDirection: 'row' },
  activityTimeColumn: { width: 54 },
  activityTime: {
    fontFamily: fonts.regular,
    fontSize: 8.5,
    color: colors.muted,
    paddingTop: 1,
  },
  activityRail: { width: 22, alignItems: 'center', alignSelf: 'stretch' },
  activityNode: {
    width: 10,
    height: 10,
    borderRadius: 5,
    backgroundColor: colors.green,
    borderWidth: 2,
    borderColor: colors.greenSoft,
  },
  activityNodeRetry: { backgroundColor: colors.amber, borderColor: '#FFF4D8' },
  activityNodeFailed: { backgroundColor: colors.coral, borderColor: colors.coralSoft },
  activityLine: { width: 1.5, flex: 1, backgroundColor: colors.lineStrong },
  activityCopy: { flex: 1, paddingBottom: 14 },
  activitySummary: { ...type.caption, color: colors.ink },
  activityReason: {
    fontFamily: fonts.regular,
    fontSize: 9,
    lineHeight: 14,
    color: colors.muted,
    marginTop: 3,
    textTransform: 'capitalize',
  },
  profileCard: {
    minHeight: 224,
    padding: 22,
    borderRadius: 20,
    alignItems: 'center',
    justifyContent: 'center',
  },
  profileAvatar: {
    width: 76,
    height: 76,
    borderRadius: 25,
    borderWidth: 3,
    borderColor: 'rgba(255,255,255,0.18)',
  },
  profileBrand: {
    width: 76,
    height: 76,
    borderRadius: 25,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: colors.surface,
  },
  profileName: { ...type.title, color: colors.surface, marginTop: 12 },
  profileEmail: { ...type.caption, color: colors.whiteMuted, marginTop: 2 },
  googleOnly: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 7,
    marginTop: 12,
    paddingHorizontal: 11,
    paddingVertical: 6,
    borderRadius: radius.pill,
    backgroundColor: 'rgba(255,255,255,0.09)',
  },
  googleLetter: { fontFamily: fonts.bold, fontSize: 12, color: '#8AB4F8' },
  googleOnlyText: { ...type.caption, color: colors.surface },
  settingsLabel: {
    ...type.caption,
    color: colors.muted,
    textTransform: 'uppercase',
    letterSpacing: 0.7,
    marginTop: 4,
    marginLeft: 2,
  },
  settingsGroup: {
    borderRadius: 16,
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.line,
    overflow: 'hidden',
  },
  settingRow: {
    minHeight: 82,
    paddingHorizontal: 13,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 11,
  },
  settingIcon: {
    width: 42,
    height: 42,
    borderRadius: 13,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: colors.primarySoft,
  },
  settingCopy: { flex: 1 },
  settingTitle: { ...type.label, color: colors.ink },
  settingSubtitle: { ...type.caption, color: colors.muted, marginTop: 2 },
  signOut: {
    minHeight: 52,
    borderRadius: 14,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 8,
    backgroundColor: colors.coralSoft,
  },
  signOutText: { ...type.label, color: colors.coral },
  authNote: {
    ...type.caption,
    color: colors.muted,
    textAlign: 'center',
    paddingHorizontal: 16,
  },
  emptyState: {
    flex: 1,
    margin: 14,
    minHeight: 240,
    padding: 24,
    borderRadius: 20,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.line,
  },
  emptyStateCompact: { flex: 0, margin: 0, minHeight: 170 },
  emptyStateIcon: {
    width: 52,
    height: 52,
    borderRadius: 17,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: colors.primarySoft,
  },
  emptyStateTitle: { ...type.section, color: colors.ink, marginTop: 12 },
  emptyStateBody: {
    ...type.body,
    color: colors.muted,
    textAlign: 'center',
    maxWidth: 290,
    marginTop: 5,
  },
  inlineError: {
    minHeight: 38,
    paddingHorizontal: 11,
    paddingVertical: 8,
    borderRadius: 12,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 7,
    backgroundColor: colors.coralSoft,
    borderWidth: 1,
    borderColor: '#F8CAD0',
  },
  inlineErrorText: { ...type.caption, color: '#A92C3A', flex: 1 },
});
