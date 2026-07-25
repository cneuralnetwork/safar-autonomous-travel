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
  Pressable,
  ScrollView,
  StyleSheet,
  Switch,
  Text,
  TextInput,
  View,
} from 'react-native';
import { FlashList } from '@shopify/flash-list';
import { Image } from 'expo-image';
import { Ionicons } from '@expo/vector-icons';
import { SafeAreaView, useSafeAreaInsets } from 'react-native-safe-area-context';
import * as WebBrowser from 'expo-web-browser';
import { useAuth } from '@/auth/AuthProvider';
import { api, ApiError } from '@/lib/api';
import { BottomDock, type TabKey } from '@/components/BottomDock';
import { BrandMark } from '@/components/BrandMark';
import { Composer } from '@/components/Composer';
import { MessageRenderer } from '@/components/MessageRenderer';
import { colors, radius, shadow, type } from '@/theme';
import type {
  ChatMessage,
  Conversation,
  ConversationSnapshot,
  OperationEvent,
} from '@/types';
import { relativeDate } from '@/utils/format';

const wait = (milliseconds: number) =>
  new Promise<void>((resolve) => {
    setTimeout(resolve, milliseconds);
  });

export function AppShell() {
  const { session, signOut } = useAuth();
  const insets = useSafeAreaInsets();
  const accessToken = session?.access_token ?? '';
  const [tab, setTab] = useState<TabKey>('plan');
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [snapshot, setSnapshot] = useState<ConversationSnapshot | null>(null);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [sending, setSending] = useState(false);
  const [approvalBusy, setApprovalBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [composerText, setComposerText] = useState('');
  const [editingApproval, setEditingApproval] = useState(false);
  const [resilienceDemo, setResilienceDemo] = useState(false);
  const composerRef = useRef<TextInput>(null);

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
      setSnapshot(await api.getConversation(accessToken, conversationId));
      setError(null);
    } catch (refreshError) {
      setError(messageForError(refreshError));
    }
  }, [accessToken, conversationId]);

  useEffect(() => {
    void refreshConversations();
  }, [refreshConversations]);

  useEffect(() => {
    if (!conversationId) {
      setSnapshot(null);
      return;
    }
    void refreshSnapshot();
    const poll = setInterval(() => void refreshSnapshot(), 1300);
    return () => clearInterval(poll);
  }, [conversationId, refreshSnapshot]);

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
        setTab('plan');
        requestAnimationFrame(() => composerRef.current?.focus());
        return;
      }
      setApprovalBusy(true);
      setEditingApproval(false);
      try {
        await api.resolveApproval(accessToken, run.id, run.approval, decision);
      } catch (approvalError) {
        if (
          decision === 'approve' &&
          approvalError instanceof ApiError &&
          approvalError.status === 409
        ) {
          const connected = await connectCalendar();
          if (!connected) throw new Error('Calendar connection was not completed.');
          await api.resolveApproval(accessToken, run.id, run.approval, 'approve');
        } else {
          throw approvalError;
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
    setTab('plan');
  }, []);

  const newTrip = useCallback(() => {
    Keyboard.dismiss();
    setConversationId(null);
    setSnapshot(null);
    setComposerText('');
    setEditingApproval(false);
    setTab('plan');
  }, []);

  const dockBottom = Math.max(12, insets.bottom + 4);
  return (
    <SafeAreaView style={styles.safe} edges={['top']}>
      <Header
        tab={tab}
        avatar={session?.user.user_metadata.avatar_url}
        onNewTrip={newTrip}
      />
      <View style={styles.content}>
        {tab === 'plan' ? (
          <PlanScreen
            snapshot={snapshot}
            onSend={sendMessage}
            sending={sending}
            error={error}
            composerText={composerText}
            setComposerText={setComposerText}
            composerRef={composerRef}
            resilienceDemo={resilienceDemo}
            setResilienceDemo={setResilienceDemo}
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
          <ActivityScreen messages={snapshot?.messages ?? []} />
        ) : null}
        {tab === 'profile' ? (
          <ProfileScreen
            accessToken={accessToken}
            name={session?.user.user_metadata.full_name || session?.user.user_metadata.name}
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
        <BottomDock active={tab} onChange={setTab} />
      </View>
    </SafeAreaView>
  );
}

function Header({
  tab,
  avatar,
  onNewTrip,
}: {
  tab: TabKey;
  avatar?: string;
  onNewTrip: () => void;
}) {
  const title = {
    plan: 'Plan with Safar',
    trips: 'Your trips',
    activity: 'Activity',
    profile: 'Your account',
  }[tab];
  return (
    <View style={styles.header}>
      <View style={styles.headerIdentity}>
        {avatar ? (
          <Image source={{ uri: avatar }} style={styles.avatar} />
        ) : (
          <BrandMark size={42} />
        )}
        <View>
          <Text style={styles.headerEyebrow}>Safar</Text>
          <Text style={styles.headerTitle}>{title}</Text>
        </View>
      </View>
      {tab === 'plan' ? (
        <Pressable
          style={({ pressed }) => [styles.headerButton, pressed && styles.pressed]}
          onPress={onNewTrip}
          accessibilityRole="button"
          accessibilityLabel="Start a new trip"
        >
          <Ionicons name="add" size={23} color={colors.ink} />
        </Pressable>
      ) : (
        <View style={styles.headerButton}>
          <Ionicons name="ellipsis-horizontal" size={21} color={colors.ink} />
        </View>
      )}
    </View>
  );
}

function PlanScreen({
  snapshot,
  onSend,
  sending,
  error,
  composerText,
  setComposerText,
  composerRef,
  resilienceDemo,
  setResilienceDemo,
  onApproval,
  approvalBusy,
  editingApproval,
}: {
  snapshot: ConversationSnapshot | null;
  onSend: (text: string) => Promise<void>;
  sending: boolean;
  error: string | null;
  composerText: string;
  setComposerText: (text: string) => void;
  composerRef: RefObject<TextInput | null>;
  resilienceDemo: boolean;
  setResilienceDemo: (value: boolean) => void;
  onApproval: (decision: 'approve' | 'edit' | 'cancel') => void;
  approvalBusy: boolean;
  editingApproval: boolean;
}) {
  const messages = snapshot?.messages ?? [];
  if (!snapshot) {
    return (
      <View style={styles.emptyScreen}>
        <ScrollView
          contentContainerStyle={styles.emptyScroll}
          keyboardShouldPersistTaps="handled"
        >
          <EmptyHero />
          <View style={styles.examples}>
            <Text style={styles.exampleLabel}>Try saying</Text>
            {[
              'A 3-day Goa trip from Kolkata under ₹30,000 next weekend',
              'Plan Jaipur with my usual preferences',
              'A quiet beach weekend, no flights before 8 am',
            ].map((example) => (
              <Pressable
                key={example}
                style={({ pressed }) => [styles.example, pressed && styles.pressed]}
                onPress={() => void onSend(example)}
              >
                <Text style={styles.exampleText}>{example}</Text>
                <Ionicons name="arrow-forward" size={16} color={colors.blue} />
              </Pressable>
            ))}
          </View>
          <View style={styles.demoRow}>
            <View style={styles.demoCopy}>
              <Text style={styles.demoTitle}>Resilience demo</Text>
              <Text style={styles.demoBody}>Inject one labelled provider timeout.</Text>
            </View>
            <Switch
              value={resilienceDemo}
              onValueChange={setResilienceDemo}
              trackColor={{ false: '#CFD1CF', true: '#9EDCF5' }}
              thumbColor={resilienceDemo ? colors.blue : '#FFFFFF'}
            />
          </View>
        </ScrollView>
        <View style={styles.emptyComposer}>
          {error ? <InlineError text={error} /> : null}
          <Composer
            ref={composerRef}
            value={composerText}
            onChangeText={setComposerText}
            onSend={onSend}
            busy={sending}
          />
        </View>
      </View>
    );
  }

  return (
    <View style={styles.chatScreen}>
      <FlashList
        data={messages}
        keyExtractor={(item) => item.id}
        keyboardShouldPersistTaps="handled"
        maintainVisibleContentPosition={{
          autoscrollToBottomThreshold: 0.25,
          startRenderingFromBottom: true,
        }}
        contentContainerStyle={styles.chatContent}
        renderItem={({ item }) => (
          <MessageRenderer
            message={item}
            activeGraph={
              item.run_id === snapshot.active_run?.id
                ? snapshot.active_run.graph
                : undefined
            }
            onQuickReply={(reply) => void onSend(reply)}
            onApproval={onApproval}
            approvalBusy={approvalBusy}
          />
        )}
        ListFooterComponent={<View style={styles.listFooter} />}
      />
      <View style={styles.chatComposer}>
        {error ? <InlineError text={error} /> : null}
        <Composer
          ref={composerRef}
          value={composerText}
          onChangeText={setComposerText}
          onSend={onSend}
          busy={sending}
          placeholder={
            editingApproval
              ? 'Describe the change…'
              : snapshot.active_run?.status === 'awaiting_input'
              ? 'Answer Safar…'
              : 'Ask Safar to change anything…'
          }
        />
      </View>
    </View>
  );
}

function EmptyHero() {
  const texture = require('../../assets/generated/route-grid.png');
  return (
    <View style={styles.emptyHero}>
      <Image source={texture} style={StyleSheet.absoluteFill} contentFit="cover" />
      <View style={styles.heroMark}>
        <Ionicons name="navigate" size={24} color={colors.surface} />
      </View>
      <Text style={styles.heroEyebrow}>No forms. Just tell me.</Text>
      <Text style={styles.heroTitle}>Where should we go?</Text>
      <Text style={styles.heroBody}>
        I’ll ask for anything missing, compare the real options, and keep the plan
        within your constraints.
      </Text>
    </View>
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
  return (
    <ScrollView contentContainerStyle={styles.tabScroll}>
      <View style={styles.tripSummary}>
        <Text style={styles.tripSummaryLabel}>Saved journeys</Text>
        <Text style={styles.tripSummaryNumber}>{conversations.length}</Text>
        <Text style={styles.tripSummaryBody}>
          Every conversation, decision and artifact stays with its trip.
        </Text>
      </View>
      <Pressable
        onPress={onNewTrip}
        style={({ pressed }) => [styles.newTripCard, pressed && styles.pressed]}
      >
        <View style={styles.newTripIcon}>
          <Ionicons name="add" size={22} color={colors.surface} />
        </View>
        <View style={styles.tripCopy}>
          <Text style={styles.tripTitle}>Plan a new trip</Text>
          <Text style={styles.tripMeta}>Start with one high-level message</Text>
        </View>
        <Ionicons name="arrow-forward" size={18} color={colors.blue} />
      </Pressable>
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
            <View style={styles.tripThumb}>
              <Image
                source={require('../../assets/generated/destination-fallback.png')}
                style={StyleSheet.absoluteFill}
                contentFit="cover"
              />
            </View>
            <View style={styles.tripCopy}>
              <Text style={styles.tripTitle}>{conversation.title}</Text>
              <Text style={styles.tripMeta} numberOfLines={1}>
                {conversation.last_message || 'Ready to continue'}
              </Text>
              <Text style={styles.tripDate}>{relativeDate(conversation.updated_at)}</Text>
            </View>
            <Ionicons name="chevron-forward" size={18} color={colors.muted} />
          </Pressable>
        ))}
      </View>
    </ScrollView>
  );
}

function ActivityScreen({ messages }: { messages: ChatMessage[] }) {
  const operations = useMemo(
    () =>
      messages
        .filter((message) => ['operation', 'error', 'calendar'].includes(message.kind))
        .slice()
        .reverse(),
    [messages],
  );
  return (
    <ScrollView contentContainerStyle={styles.tabScroll}>
      <View style={styles.activityHero}>
        <Text style={styles.activityEyebrow}>Transparent by default</Text>
        <Text style={styles.activityTitle}>What Safar did</Text>
        <Text style={styles.activityBody}>
          Operational explanations only — never hidden reasoning or private chain-of-thought.
        </Text>
      </View>
      {operations.length ? (
        operations.map((message, index) => {
          const event = message.payload.event as OperationEvent | undefined;
          const status = event?.status || (message.kind === 'error' ? 'failed' : 'completed');
          return (
            <View key={message.id} style={styles.activityRow}>
              <View style={styles.timeline}>
                <View
                  style={[
                    styles.timelineDot,
                    status === 'retrying' && styles.timelineRetry,
                    status === 'failed' && styles.timelineFailed,
                  ]}
                />
                {index < operations.length - 1 ? <View style={styles.timelineLine} /> : null}
              </View>
              <View style={styles.activityCopy}>
                <Text style={styles.activitySummary}>{event?.summary || message.text}</Text>
                <Text style={styles.activityReason}>
                  {event?.reason || relativeDate(message.created_at)}
                </Text>
              </View>
            </View>
          );
        })
      ) : (
        <View style={styles.noActivity}>
          <Ionicons name="pulse-outline" size={28} color={colors.blue} />
          <Text style={styles.noActivityTitle}>No execution yet</Text>
          <Text style={styles.noActivityBody}>Start a trip and its live action log appears here.</Text>
        </View>
      )}
    </ScrollView>
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
  name?: string;
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
    <ScrollView contentContainerStyle={styles.tabScroll}>
      <View style={styles.profileCard}>
        {avatar ? (
          <Image source={{ uri: avatar }} style={styles.profileAvatar} />
        ) : (
          <BrandMark size={68} />
        )}
        <Text style={styles.profileName}>{name || 'Traveller'}</Text>
        <Text style={styles.profileEmail}>{email}</Text>
        <View style={styles.googleOnly}>
          <Text style={styles.googleOnlyG}>G</Text>
          <Text style={styles.googleOnlyText}>Signed in with Google</Text>
        </View>
      </View>
      <Text style={styles.settingsLabel}>Connections</Text>
      <View style={styles.settingsGroup}>
        <SettingRow
          icon="calendar"
          title="Google Calendar"
          subtitle={
            calendarConnected
              ? 'Connected · itinerary writes still need approval'
              : 'Connect only when you want to add a trip'
          }
          action={
            calendarBusy ? (
              <ActivityIndicator size="small" color={colors.blue} />
            ) : (
              <Switch
                value={calendarConnected}
                onValueChange={() => void toggleCalendar()}
                trackColor={{ false: '#CFD1CF', true: '#9EDCF5' }}
                thumbColor={calendarConnected ? colors.blue : '#FFFFFF'}
              />
            )
          }
        />
      </View>
      <Text style={styles.settingsLabel}>Demo lab</Text>
      <View style={styles.settingsGroup}>
        <SettingRow
          icon="flask"
          title="Resilience demo"
          subtitle="Labels and recovers from one injected provider timeout"
          action={
            <Switch
              value={resilienceDemo}
              onValueChange={setResilienceDemo}
              trackColor={{ false: '#CFD1CF', true: '#F3B9B2' }}
              thumbColor={resilienceDemo ? colors.coral : '#FFFFFF'}
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
        Safar intentionally provides no password, magic-link, phone, guest, Apple or
        alternate-provider account path.
      </Text>
    </ScrollView>
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
        <Ionicons name={icon} size={19} color={colors.ink} />
      </View>
      <View style={styles.settingCopy}>
        <Text style={styles.settingTitle}>{title}</Text>
        <Text style={styles.settingSubtitle}>{subtitle}</Text>
      </View>
      {action}
    </View>
  );
}

function InlineError({ text }: { text: string }) {
  return (
    <View style={styles.inlineError}>
      <Ionicons name="alert-circle" size={15} color={colors.coral} />
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

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.canvas },
  content: { flex: 1 },
  header: {
    height: 74,
    paddingHorizontal: 18,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  headerIdentity: { flexDirection: 'row', alignItems: 'center', gap: 11 },
  avatar: { width: 42, height: 42, borderRadius: 15, backgroundColor: colors.surface },
  headerEyebrow: { ...type.caption, color: colors.muted },
  headerTitle: { ...type.section, color: colors.ink },
  headerButton: {
    width: 46,
    height: 46,
    borderRadius: 23,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.line,
  },
  dockWrap: { position: 'absolute', left: 18, right: 18, zIndex: 30 },
  emptyScreen: { flex: 1 },
  emptyScroll: { padding: 18, paddingBottom: 180, gap: 22 },
  emptyHero: {
    minHeight: 310,
    overflow: 'hidden',
    borderRadius: 30,
    backgroundColor: colors.surface,
    padding: 24,
    justifyContent: 'flex-end',
    borderWidth: 1,
    borderColor: colors.line,
    ...shadow,
  },
  heroMark: {
    position: 'absolute',
    top: 22,
    left: 22,
    width: 50,
    height: 50,
    borderRadius: 17,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: colors.ink,
  },
  heroEyebrow: {
    ...type.caption,
    color: colors.blue,
    textTransform: 'uppercase',
    letterSpacing: 0.9,
  },
  heroTitle: { ...type.hero, color: colors.ink, marginTop: 7 },
  heroBody: { ...type.body, color: colors.muted, marginTop: 10, maxWidth: 330 },
  examples: { gap: 9 },
  exampleLabel: {
    ...type.caption,
    color: colors.muted,
    textTransform: 'uppercase',
    letterSpacing: 0.8,
    marginLeft: 3,
  },
  example: {
    minHeight: 58,
    borderRadius: radius.medium,
    paddingHorizontal: 16,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.line,
  },
  exampleText: { ...type.label, color: colors.ink, flex: 1 },
  demoRow: {
    minHeight: 70,
    borderRadius: radius.medium,
    backgroundColor: '#F6F6F4',
    paddingHorizontal: 16,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
  },
  demoCopy: { flex: 1 },
  demoTitle: { ...type.label, color: colors.ink },
  demoBody: { ...type.caption, color: colors.muted, marginTop: 2 },
  emptyComposer: { position: 'absolute', left: 18, right: 18, bottom: 92, gap: 7 },
  chatScreen: { flex: 1 },
  chatContent: { paddingHorizontal: 17, paddingTop: 8 },
  listFooter: { height: 190 },
  chatComposer: { position: 'absolute', left: 17, right: 17, bottom: 92, gap: 7 },
  inlineError: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 7,
    borderRadius: 12,
    backgroundColor: colors.coralSoft,
    paddingHorizontal: 11,
    paddingVertical: 8,
  },
  inlineErrorText: { ...type.caption, color: '#9B3F37', flex: 1 },
  tabScroll: { padding: 18, paddingBottom: 118, gap: 16 },
  tripSummary: {
    minHeight: 180,
    borderRadius: 28,
    backgroundColor: colors.ink,
    padding: 22,
    justifyContent: 'flex-end',
  },
  tripSummaryLabel: {
    ...type.caption,
    color: '#AEB0B2',
    textTransform: 'uppercase',
    letterSpacing: 0.8,
  },
  tripSummaryNumber: {
    fontSize: 48,
    lineHeight: 52,
    fontWeight: '800',
    color: colors.surface,
    letterSpacing: -1.4,
  },
  tripSummaryBody: { ...type.body, color: '#B6B8BA', marginTop: 7, maxWidth: 300 },
  newTripCard: {
    minHeight: 78,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 13,
    borderRadius: radius.large,
    backgroundColor: colors.surface,
    padding: 13,
    borderWidth: 1,
    borderColor: colors.line,
  },
  newTripIcon: {
    width: 48,
    height: 48,
    borderRadius: 16,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: colors.blue,
  },
  tripList: { gap: 10 },
  tripCard: {
    minHeight: 96,
    borderRadius: radius.large,
    backgroundColor: colors.surface,
    padding: 11,
    borderWidth: 1,
    borderColor: colors.line,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
  },
  tripCardActive: { borderColor: colors.blue },
  tripThumb: {
    width: 72,
    height: 72,
    borderRadius: 18,
    overflow: 'hidden',
    backgroundColor: colors.canvas,
  },
  tripCopy: { flex: 1, minWidth: 0 },
  tripTitle: { ...type.label, color: colors.ink },
  tripMeta: { ...type.caption, color: colors.muted, marginTop: 4 },
  tripDate: { ...type.caption, color: colors.blue, marginTop: 5 },
  activityHero: {
    minHeight: 210,
    borderRadius: 28,
    backgroundColor: colors.surface,
    padding: 22,
    justifyContent: 'flex-end',
    borderWidth: 1,
    borderColor: colors.line,
  },
  activityEyebrow: {
    ...type.caption,
    color: colors.blue,
    textTransform: 'uppercase',
    letterSpacing: 0.8,
  },
  activityTitle: { ...type.title, color: colors.ink, marginTop: 6 },
  activityBody: { ...type.body, color: colors.muted, marginTop: 8 },
  activityRow: { flexDirection: 'row', minHeight: 72, gap: 13 },
  timeline: { alignItems: 'center', width: 20 },
  timelineDot: {
    width: 13,
    height: 13,
    borderRadius: 99,
    backgroundColor: colors.green,
    marginTop: 5,
  },
  timelineRetry: { backgroundColor: colors.amber },
  timelineFailed: { backgroundColor: colors.coral },
  timelineLine: { width: 1, flex: 1, backgroundColor: colors.line, marginVertical: 5 },
  activityCopy: { flex: 1, paddingBottom: 18 },
  activitySummary: { ...type.label, color: colors.ink },
  activityReason: { ...type.caption, color: colors.muted, marginTop: 4 },
  noActivity: {
    minHeight: 180,
    borderRadius: radius.large,
    alignItems: 'center',
    justifyContent: 'center',
    gap: 7,
    backgroundColor: colors.surface,
  },
  noActivityTitle: { ...type.section, color: colors.ink },
  noActivityBody: { ...type.body, color: colors.muted, textAlign: 'center', maxWidth: 280 },
  profileCard: {
    alignItems: 'center',
    borderRadius: 28,
    padding: 26,
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.line,
  },
  profileAvatar: { width: 76, height: 76, borderRadius: 26 },
  profileName: { ...type.title, color: colors.ink, marginTop: 15 },
  profileEmail: { ...type.body, color: colors.muted, marginTop: 3 },
  googleOnly: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 7,
    marginTop: 15,
    paddingHorizontal: 12,
    paddingVertical: 7,
    borderRadius: radius.pill,
    backgroundColor: colors.canvas,
  },
  googleOnlyG: { fontSize: 13, fontWeight: '800', color: '#4285F4' },
  googleOnlyText: { ...type.caption, color: colors.ink },
  settingsLabel: {
    ...type.caption,
    color: colors.muted,
    textTransform: 'uppercase',
    letterSpacing: 0.8,
    marginTop: 7,
    marginLeft: 3,
  },
  settingsGroup: {
    borderRadius: radius.large,
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.line,
    overflow: 'hidden',
  },
  settingRow: {
    minHeight: 82,
    paddingHorizontal: 14,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
  },
  settingIcon: {
    width: 42,
    height: 42,
    borderRadius: 14,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: colors.canvas,
  },
  settingCopy: { flex: 1 },
  settingTitle: { ...type.label, color: colors.ink },
  settingSubtitle: { ...type.caption, color: colors.muted, marginTop: 3 },
  signOut: {
    minHeight: 56,
    borderRadius: radius.medium,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 9,
    backgroundColor: colors.coralSoft,
  },
  signOutText: { ...type.label, color: colors.coral },
  authNote: { ...type.caption, color: colors.muted, textAlign: 'center', paddingHorizontal: 18 },
  pressed: { opacity: 0.72, transform: [{ scale: 0.985 }] },
});
