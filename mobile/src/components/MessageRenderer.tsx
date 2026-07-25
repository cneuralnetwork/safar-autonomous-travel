import { memo } from 'react';
import {
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import { Image } from 'expo-image';
import { Ionicons } from '@expo/vector-icons';
import type {
  Approval,
  ChatMessage,
  FlightOption,
  HotelOption,
  Itinerary,
  OperationEvent,
  PackageOption,
  TaskGraph,
  TravelConstraints,
} from '@/types';
import { colors, radius, shadow, type } from '@/theme';
import { formatCurrency, formatDate, formatTime } from '@/utils/format';
import { ItineraryMap } from '@/components/ItineraryMap';

interface MessageRendererProps {
  message: ChatMessage;
  onQuickReply: (text: string) => void;
  onApproval: (decision: 'approve' | 'edit' | 'cancel') => void;
  approvalBusy: boolean;
}

export const MessageRenderer = memo(function MessageRenderer({
  message,
  onQuickReply,
  onApproval,
  approvalBusy,
}: MessageRendererProps) {
  if (message.role === 'user') {
    return (
      <View style={styles.userRow}>
        <View style={styles.userBubble}>
          <Text style={styles.userText}>{message.text}</Text>
        </View>
      </View>
    );
  }

  switch (message.kind) {
    case 'interpretation':
      return (
        <InterpretationCard
          text={message.text}
          constraints={message.payload.constraints as TravelConstraints}
        />
      );
    case 'clarification':
      return (
        <ClarificationCard
          text={message.text}
          replies={(message.payload.quick_replies as string[]) || []}
          onReply={onQuickReply}
        />
      );
    case 'task_graph':
      return <TaskGraphCard graph={message.payload.graph as TaskGraph} />;
    case 'operation':
      return <OperationRow event={message.payload.event as OperationEvent} />;
    case 'flight_options':
      return <FlightCards flights={(message.payload.flights as FlightOption[]) || []} />;
    case 'hotel_options':
      return <HotelCards hotels={(message.payload.hotels as HotelOption[]) || []} />;
    case 'budget':
      return (
        <BudgetCard
          selected={message.payload.selected_package as PackageOption}
          rejectedCount={(message.payload.rejected_count as number) || 0}
        />
      );
    case 'itinerary':
      return <ItineraryCard itinerary={message.payload.itinerary as Itinerary} />;
    case 'approval':
      return (
        <ApprovalCard
          approval={message.payload.approval as Approval}
          busy={approvalBusy}
          onDecision={onApproval}
        />
      );
    case 'calendar':
      return (
        <CalendarResult
          text={message.text}
          links={(message.payload.links as string[]) || []}
        />
      );
    case 'report':
      return <ReportCard report={message.payload.report as Record<string, unknown>} />;
    case 'error':
      return <SystemCard text={message.text} tone="error" />;
    default:
      return <SystemCard text={message.text} />;
  }
});

function CardHeader({
  icon,
  eyebrow,
  title,
}: {
  icon: keyof typeof Ionicons.glyphMap;
  eyebrow: string;
  title: string;
}) {
  return (
    <View style={styles.cardHeader}>
      <View style={styles.iconTile}>
        <Ionicons name={icon} size={18} color={colors.ink} />
      </View>
      <View style={styles.cardHeaderCopy}>
        <Text style={styles.eyebrow}>{eyebrow}</Text>
        <Text style={styles.cardTitle}>{title}</Text>
      </View>
    </View>
  );
}

function InterpretationCard({
  text,
  constraints,
}: {
  text: string;
  constraints?: TravelConstraints;
}) {
  if (!constraints) return <SystemCard text={text} />;
  const chips = [
    constraints.origin && constraints.destination
      ? `${constraints.origin} → ${constraints.destination}`
      : null,
    constraints.duration_days ? `${constraints.duration_days} days` : null,
    constraints.adults ? `${constraints.adults} traveller${constraints.adults > 1 ? 's' : ''}` : null,
    constraints.budget ? `Under ${formatCurrency(constraints.budget)}` : null,
    constraints.earliest_departure
      ? `After ${constraints.earliest_departure.slice(0, 5)}`
      : null,
    constraints.hotel_area_preference
      ? `Near ${constraints.hotel_area_preference}`
      : null,
  ].filter(Boolean) as string[];
  return (
    <View style={styles.card}>
      <CardHeader icon="sparkles" eyebrow="I understood" title="Your trip brief" />
      <Text style={styles.body}>{text}</Text>
      <View style={styles.chipWrap}>
        {chips.map((chip) => (
          <View key={chip} style={styles.chip}>
            <Text style={styles.chipText}>{chip}</Text>
          </View>
        ))}
      </View>
    </View>
  );
}

function ClarificationCard({
  text,
  replies,
  onReply,
}: {
  text: string;
  replies: string[];
  onReply: (text: string) => void;
}) {
  return (
    <View style={styles.assistantBlock}>
      <View style={styles.assistantDot}>
        <Ionicons name="navigate" size={14} color={colors.surface} />
      </View>
      <View style={styles.assistantCopy}>
        <Text style={styles.assistantText}>{text}</Text>
        {replies.length ? (
          <View style={styles.replyStack}>
            {replies.map((reply) => (
              <Pressable
                key={reply}
                onPress={() => onReply(reply)}
                style={({ pressed }) => [styles.reply, pressed && styles.pressed]}
              >
                <Text style={styles.replyText}>{reply}</Text>
                <Ionicons name="arrow-forward" size={15} color={colors.blue} />
              </Pressable>
            ))}
          </View>
        ) : null}
      </View>
    </View>
  );
}

function TaskGraphCard({ graph }: { graph?: TaskGraph }) {
  if (!graph) return null;
  const statusColors = {
    waiting: '#B9BBBA',
    running: colors.blue,
    completed: colors.green,
    retrying: colors.amber,
    failed: colors.coral,
    awaiting_approval: colors.coral,
    skipped: '#B9BBBA',
  };
  const flight = graph.tasks.find((task) => task.id === 'flight_search');
  const hotel = graph.tasks.find((task) => task.id === 'hotel_search');
  const linear = graph.tasks.filter(
    (task) => !['flight_search', 'hotel_search'].includes(task.id),
  );
  const node = (task: (typeof graph.tasks)[number]) => (
    <View key={task.id} style={styles.graphNode}>
      <View
        style={[
          styles.statusDot,
          { backgroundColor: statusColors[task.status] },
          task.status === 'running' && styles.statusRing,
        ]}
      />
      <View style={styles.graphNodeCopy}>
        <Text style={styles.graphNodeTitle}>{task.title}</Text>
        <Text style={styles.graphNodeStatus}>
          {task.status.replace('_', ' ')}
          {task.attempts > 1 ? ` · ${task.attempts} attempts` : ''}
        </Text>
      </View>
    </View>
  );
  return (
    <View style={styles.card}>
      <CardHeader icon="git-network" eyebrow="Execution plan" title={graph.goal} />
      <View style={styles.graph}>
        {linear.slice(0, 2).map(node)}
        <View style={styles.parallel}>
          {flight ? <View style={styles.parallelNode}>{node(flight)}</View> : null}
          {hotel ? <View style={styles.parallelNode}>{node(hotel)}</View> : null}
        </View>
        {linear.slice(2).map(node)}
      </View>
    </View>
  );
}

function OperationRow({ event }: { event?: OperationEvent }) {
  if (!event) return null;
  const icon =
    event.status === 'completed'
      ? 'checkmark'
      : event.status === 'retrying'
        ? 'refresh'
        : event.status === 'failed'
          ? 'alert'
          : 'ellipsis-horizontal';
  return (
    <View style={styles.operation}>
      <View
        style={[
          styles.operationIcon,
          event.status === 'retrying' && styles.operationRetry,
          event.status === 'failed' && styles.operationError,
        ]}
      >
        <Ionicons name={icon} size={13} color={colors.surface} />
      </View>
      <View style={styles.operationCopy}>
        <Text style={styles.operationSummary}>{event.summary}</Text>
        {event.reason ? <Text style={styles.operationReason}>{event.reason}</Text> : null}
      </View>
    </View>
  );
}

function FlightCards({ flights }: { flights: FlightOption[] }) {
  return (
    <View>
      <Text style={styles.sectionLabel}>Flights</Text>
      <ScrollView
        horizontal
        showsHorizontalScrollIndicator={false}
        contentContainerStyle={styles.carousel}
      >
        {flights.slice(0, 5).map((flight, index) => {
          const outbound = flight.outbound[0];
          const arrival = flight.outbound.at(-1);
          if (!outbound || !arrival) return null;
          return (
            <View key={flight.id} style={styles.optionCard}>
              <View style={styles.optionTop}>
                <View style={styles.airlineBadge}>
                  <Ionicons name="airplane" size={17} color={colors.ink} />
                </View>
                <Text style={styles.optionRank}>{index === 0 ? 'Best match' : `Option ${index + 1}`}</Text>
                <Text style={styles.optionPrice}>{formatCurrency(flight.total_price)}</Text>
              </View>
              <View style={styles.routeRow}>
                <View>
                  <Text style={styles.airport}>{outbound.departure_airport}</Text>
                  <Text style={styles.mini}>{formatTime(outbound.departure_at)}</Text>
                </View>
                <View style={styles.routeLine}>
                  <View style={styles.routeDot} />
                  <View style={styles.routeStroke} />
                  <Ionicons name="airplane" size={14} color={colors.blue} />
                </View>
                <View style={styles.routeArrival}>
                  <Text style={styles.airport}>{arrival.arrival_airport}</Text>
                  <Text style={styles.mini}>{formatTime(arrival.arrival_at)}</Text>
                </View>
              </View>
              <Text style={styles.optionMeta}>
                {outbound.airline} · {flight.stops ? `${flight.stops} stop` : 'Nonstop'} ·{' '}
                {flight.provider}
              </Text>
            </View>
          );
        })}
      </ScrollView>
    </View>
  );
}

function HotelCards({ hotels }: { hotels: HotelOption[] }) {
  const fallback = require('../../assets/generated/destination-fallback.png');
  return (
    <View>
      <Text style={styles.sectionLabel}>Stays</Text>
      <ScrollView
        horizontal
        showsHorizontalScrollIndicator={false}
        contentContainerStyle={styles.carousel}
      >
        {hotels.slice(0, 5).map((hotel, index) => (
          <View key={hotel.id} style={styles.hotelCard}>
            <Image
              source={hotel.image_url ? { uri: hotel.image_url } : fallback}
              style={styles.hotelImage}
              contentFit="cover"
              transition={180}
            />
            <View style={styles.hotelCopy}>
              <Text style={styles.hotelName} numberOfLines={1}>
                {hotel.name}
              </Text>
              <View style={styles.hotelMetaRow}>
                <Ionicons name="star" size={13} color={colors.amber} />
                <Text style={styles.hotelMeta}>{hotel.rating || 'New'}</Text>
                {hotel.distance_to_preference_km != null ? (
                  <Text style={styles.hotelMeta}>· {hotel.distance_to_preference_km} km</Text>
                ) : null}
              </View>
              <Text style={styles.hotelPrice}>
                {formatCurrency(hotel.total_price)}
                <Text style={styles.hotelTotal}> total</Text>
              </Text>
              {index === 0 ? (
                <View style={styles.bestPill}>
                  <Text style={styles.bestPillText}>Best value</Text>
                </View>
              ) : null}
            </View>
          </View>
        ))}
      </ScrollView>
    </View>
  );
}

function BudgetCard({
  selected,
  rejectedCount,
}: {
  selected?: PackageOption;
  rejectedCount: number;
}) {
  if (!selected) return null;
  const rows = [
    ['Return flights', selected.flight.total_price],
    [`${selected.hotel.name}`, selected.hotel.total_price],
    ['Daily trip reserve', selected.on_trip_reserve],
    ['Local transfers', selected.local_transfer_reserve],
  ] as const;
  return (
    <View style={[styles.card, styles.darkCard]}>
      <Text style={styles.darkEyebrow}>Best valid combination</Text>
      <Text style={styles.darkTotal}>{formatCurrency(selected.total_price)}</Text>
      <Text style={styles.darkSub}>
        {formatCurrency(selected.remaining_budget)} remains in your budget
      </Text>
      <View style={styles.budgetRows}>
        {rows.map(([label, value]) => (
          <View key={label} style={styles.budgetRow}>
            <Text style={styles.budgetLabel}>{label}</Text>
            <Text style={styles.budgetValue}>{formatCurrency(value)}</Text>
          </View>
        ))}
      </View>
      <View style={styles.rejectedLine}>
        <Ionicons name="shield-checkmark" size={15} color={colors.blue} />
        <Text style={styles.rejectedText}>
          {rejectedCount} combinations rejected for violating your constraints
        </Text>
      </View>
    </View>
  );
}

function ItineraryCard({ itinerary }: { itinerary?: Itinerary }) {
  if (!itinerary) return null;
  return (
    <View style={styles.card}>
      <CardHeader
        icon="map"
        eyebrow={`${itinerary.days.length}-day itinerary`}
        title="A route that actually fits"
      />
      <ItineraryMap itinerary={itinerary} />
      <View style={styles.days}>
        {itinerary.days.map((day, dayIndex) => (
          <View key={day.date} style={styles.day}>
            <View style={styles.dayIndex}>
              <Text style={styles.dayIndexText}>{dayIndex + 1}</Text>
            </View>
            <View style={styles.dayCopy}>
              <Text style={styles.dayDate}>{formatDate(day.date)}</Text>
              <Text style={styles.dayTitle}>{day.title}</Text>
              {day.items.slice(0, 4).map((item) => (
                <View key={item.id} style={styles.itineraryRow}>
                  <Text style={styles.itemTime}>{formatTime(item.start_at)}</Text>
                  <Text style={styles.itemTitle} numberOfLines={1}>
                    {item.title}
                  </Text>
                </View>
              ))}
            </View>
          </View>
        ))}
      </View>
    </View>
  );
}

function ApprovalCard({
  approval,
  busy,
  onDecision,
}: {
  approval?: Approval;
  busy: boolean;
  onDecision: (decision: 'approve' | 'edit' | 'cancel') => void;
}) {
  if (!approval) return null;
  return (
    <View style={[styles.card, styles.approvalCard]}>
      <View style={styles.approvalIcon}>
        <Ionicons name="calendar" size={24} color={colors.ink} />
      </View>
      <Text style={styles.approvalTitle}>Ready for your calendar</Text>
      <Text style={styles.approvalBody}>
        Add {approval.event_count} detailed events · estimated trip total{' '}
        {formatCurrency(approval.estimated_trip_total)}
      </Text>
      <View style={styles.safetyRow}>
        <Ionicons name="lock-closed" size={14} color={colors.green} />
        <Text style={styles.safetyText}>{approval.disclaimer}</Text>
      </View>
      <View style={styles.approvalActions}>
        <Pressable
          onPress={() => onDecision('approve')}
          disabled={busy}
          style={({ pressed }) => [styles.approve, pressed && styles.pressed]}
        >
          <Text style={styles.approveText}>{busy ? 'Working…' : 'Approve'}</Text>
        </Pressable>
        <Pressable
          onPress={() => onDecision('edit')}
          disabled={busy}
          style={({ pressed }) => [styles.secondary, pressed && styles.pressed]}
        >
          <Text style={styles.secondaryText}>Edit</Text>
        </Pressable>
        <Pressable onPress={() => onDecision('cancel')} disabled={busy}>
          <Text style={styles.cancelText}>Cancel</Text>
        </Pressable>
      </View>
    </View>
  );
}

function CalendarResult({ text, links }: { text: string; links: string[] }) {
  const source = require('../../assets/generated/goa-postcard.png');
  return (
    <View style={styles.card}>
      <Image source={source} style={styles.postcard} contentFit="cover" />
      <View style={styles.successTitleRow}>
        <View style={styles.successIcon}>
          <Ionicons name="checkmark" size={17} color={colors.surface} />
        </View>
        <Text style={styles.cardTitle}>{text}</Text>
      </View>
      {links.length ? (
        <Text style={styles.body}>{links.length} Google Calendar links are saved in the report.</Text>
      ) : (
        <Text style={styles.body}>The itinerary remains saved in your trip history.</Text>
      )}
    </View>
  );
}

function ReportCard({ report }: { report?: Record<string, unknown> }) {
  if (!report) return null;
  const metrics = [
    ['Tasks', (report.task_graph as TaskGraph | undefined)?.estimated_steps ?? 0],
    ['Tools', Number(report.tools_called || 0)],
    ['Retries', Number(report.retries || 0)],
    ['Savings', formatCurrency(Number(report.estimated_savings || 0))],
  ];
  return (
    <View style={styles.card}>
      <CardHeader icon="document-text" eyebrow="Execution report" title="Everything accounted for" />
      <View style={styles.metricGrid}>
        {metrics.map(([label, value]) => (
          <View key={String(label)} style={styles.metric}>
            <Text style={styles.metricValue}>{String(value)}</Text>
            <Text style={styles.metricLabel}>{label}</Text>
          </View>
        ))}
      </View>
      <Text style={styles.body}>
        Safar recorded the interpreted constraints, provider calls, rejected combinations,
        selected package, itinerary, approval, and calendar result.
      </Text>
    </View>
  );
}

function SystemCard({ text, tone }: { text: string; tone?: 'error' }) {
  return (
    <View style={[styles.assistantBlock, tone === 'error' && styles.errorBlock]}>
      <View style={[styles.assistantDot, tone === 'error' && styles.errorDot]}>
        <Ionicons name={tone === 'error' ? 'alert' : 'navigate'} size={14} color={colors.surface} />
      </View>
      <Text style={styles.assistantText}>{text}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  userRow: { alignItems: 'flex-end', marginVertical: 5 },
  userBubble: {
    maxWidth: '86%',
    backgroundColor: colors.ink,
    borderRadius: 22,
    borderBottomRightRadius: 7,
    paddingHorizontal: 17,
    paddingVertical: 13,
  },
  userText: { ...type.body, color: colors.surface },
  card: {
    marginVertical: 7,
    padding: 18,
    borderRadius: radius.large,
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.line,
    gap: 15,
    ...shadow,
  },
  cardHeader: { flexDirection: 'row', alignItems: 'center', gap: 12 },
  iconTile: {
    width: 42,
    height: 42,
    borderRadius: 15,
    backgroundColor: colors.canvas,
    alignItems: 'center',
    justifyContent: 'center',
  },
  cardHeaderCopy: { flex: 1, gap: 1 },
  eyebrow: { ...type.caption, color: colors.muted, textTransform: 'uppercase', letterSpacing: 0.8 },
  cardTitle: { ...type.section, color: colors.ink },
  body: { ...type.body, color: colors.muted },
  chipWrap: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  chip: {
    paddingHorizontal: 11,
    paddingVertical: 7,
    borderRadius: radius.pill,
    backgroundColor: colors.blueSoft,
  },
  chipText: { ...type.caption, color: '#0877A6' },
  assistantBlock: {
    marginVertical: 7,
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: 10,
    paddingRight: 24,
  },
  assistantDot: {
    width: 30,
    height: 30,
    borderRadius: 11,
    backgroundColor: colors.blue,
    alignItems: 'center',
    justifyContent: 'center',
    marginTop: 1,
  },
  assistantCopy: { flex: 1, gap: 12 },
  assistantText: { ...type.body, color: colors.ink, flex: 1 },
  replyStack: { gap: 8 },
  reply: {
    minHeight: 46,
    borderRadius: 15,
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.line,
    paddingHorizontal: 14,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  replyText: { ...type.label, color: colors.ink },
  graph: { gap: 5, position: 'relative' },
  graphNode: {
    minHeight: 48,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 11,
    paddingVertical: 5,
  },
  graphNodeCopy: { flex: 1 },
  graphNodeTitle: { ...type.label, color: colors.ink },
  graphNodeStatus: { ...type.caption, color: colors.muted, textTransform: 'capitalize' },
  statusDot: { width: 11, height: 11, borderRadius: 99, marginLeft: 3 },
  statusRing: { borderWidth: 3, borderColor: colors.blueSoft, width: 16, height: 16 },
  parallel: { flexDirection: 'row', gap: 8 },
  parallelNode: {
    flex: 1,
    backgroundColor: colors.canvas,
    borderRadius: 14,
    paddingHorizontal: 10,
  },
  operation: {
    marginVertical: 3,
    marginLeft: 9,
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: 10,
    opacity: 0.94,
  },
  operationIcon: {
    width: 24,
    height: 24,
    borderRadius: 9,
    backgroundColor: colors.green,
    alignItems: 'center',
    justifyContent: 'center',
  },
  operationRetry: { backgroundColor: colors.amber },
  operationError: { backgroundColor: colors.coral },
  operationCopy: { flex: 1, paddingTop: 1 },
  operationSummary: { ...type.label, color: colors.ink },
  operationReason: { ...type.caption, color: colors.muted, marginTop: 2 },
  sectionLabel: { ...type.section, color: colors.ink, marginTop: 12, marginBottom: 9 },
  carousel: { gap: 10, paddingRight: 18 },
  optionCard: {
    width: 286,
    borderRadius: radius.large,
    backgroundColor: colors.surface,
    padding: 17,
    borderWidth: 1,
    borderColor: colors.line,
    gap: 17,
    ...shadow,
  },
  optionTop: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  airlineBadge: {
    width: 32,
    height: 32,
    borderRadius: 12,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: colors.blueSoft,
  },
  optionRank: { ...type.caption, color: colors.muted, flex: 1 },
  optionPrice: { ...type.label, color: colors.ink },
  routeRow: { flexDirection: 'row', alignItems: 'center' },
  airport: { fontSize: 22, lineHeight: 25, fontWeight: '800', color: colors.ink },
  mini: { ...type.caption, color: colors.muted, marginTop: 2 },
  routeLine: {
    flex: 1,
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 10,
  },
  routeDot: { width: 6, height: 6, borderRadius: 99, backgroundColor: colors.blue },
  routeStroke: { flex: 1, height: 1, backgroundColor: colors.blue },
  routeArrival: { alignItems: 'flex-end' },
  optionMeta: { ...type.caption, color: colors.muted },
  hotelCard: {
    width: 245,
    borderRadius: radius.large,
    overflow: 'hidden',
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.line,
    ...shadow,
  },
  hotelImage: { width: '100%', height: 118, backgroundColor: colors.canvas },
  hotelCopy: { padding: 14, gap: 7 },
  hotelName: { ...type.section, color: colors.ink },
  hotelMetaRow: { flexDirection: 'row', alignItems: 'center', gap: 4 },
  hotelMeta: { ...type.caption, color: colors.muted },
  hotelPrice: { ...type.label, color: colors.ink },
  hotelTotal: { ...type.caption, color: colors.muted },
  bestPill: {
    alignSelf: 'flex-start',
    paddingHorizontal: 9,
    paddingVertical: 5,
    borderRadius: radius.pill,
    backgroundColor: colors.blueSoft,
  },
  bestPillText: { ...type.caption, color: '#0877A6' },
  darkCard: { backgroundColor: colors.ink, borderColor: colors.ink, gap: 9 },
  darkEyebrow: { ...type.caption, color: '#9DA0A2', textTransform: 'uppercase', letterSpacing: 0.8 },
  darkTotal: { fontSize: 38, lineHeight: 43, fontWeight: '800', color: colors.surface, letterSpacing: -1 },
  darkSub: { ...type.label, color: colors.blue },
  budgetRows: {
    borderTopWidth: 1,
    borderTopColor: '#393B3D',
    marginTop: 10,
    paddingTop: 8,
  },
  budgetRow: { flexDirection: 'row', justifyContent: 'space-between', paddingVertical: 5 },
  budgetLabel: { ...type.caption, color: '#B4B6B8', flex: 1 },
  budgetValue: { ...type.caption, color: colors.surface },
  rejectedLine: { flexDirection: 'row', alignItems: 'center', gap: 7, marginTop: 6 },
  rejectedText: { ...type.caption, color: '#B4B6B8', flex: 1 },
  days: { gap: 18 },
  day: { flexDirection: 'row', gap: 12 },
  dayIndex: {
    width: 34,
    height: 34,
    borderRadius: 12,
    backgroundColor: colors.ink,
    alignItems: 'center',
    justifyContent: 'center',
  },
  dayIndexText: { ...type.label, color: colors.surface },
  dayCopy: { flex: 1 },
  dayDate: { ...type.caption, color: colors.blue, textTransform: 'uppercase' },
  dayTitle: { ...type.label, color: colors.ink, marginBottom: 8 },
  itineraryRow: { flexDirection: 'row', gap: 9, marginTop: 4 },
  itemTime: { ...type.caption, color: colors.muted, width: 56 },
  itemTitle: { ...type.caption, color: colors.ink, flex: 1 },
  approvalCard: { borderColor: '#BFE5F5', backgroundColor: '#FCFEFF' },
  approvalIcon: {
    width: 50,
    height: 50,
    borderRadius: 17,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: colors.blueSoft,
  },
  approvalTitle: { ...type.title, color: colors.ink },
  approvalBody: { ...type.body, color: colors.muted },
  safetyRow: { flexDirection: 'row', gap: 7, alignItems: 'center' },
  safetyText: { ...type.caption, color: colors.green, flex: 1 },
  approvalActions: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  approve: {
    flex: 1,
    minHeight: 48,
    borderRadius: 16,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: colors.ink,
  },
  approveText: { ...type.label, color: colors.surface },
  secondary: {
    minHeight: 48,
    paddingHorizontal: 19,
    borderRadius: 16,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: colors.canvas,
  },
  secondaryText: { ...type.label, color: colors.ink },
  cancelText: { ...type.label, color: colors.coral, padding: 8 },
  postcard: { width: '100%', height: 145, borderRadius: radius.medium },
  successTitleRow: { flexDirection: 'row', alignItems: 'center', gap: 9 },
  successIcon: {
    width: 30,
    height: 30,
    borderRadius: 11,
    backgroundColor: colors.green,
    alignItems: 'center',
    justifyContent: 'center',
  },
  metricGrid: { flexDirection: 'row', gap: 7 },
  metric: {
    flex: 1,
    minHeight: 70,
    borderRadius: 15,
    backgroundColor: colors.canvas,
    justifyContent: 'center',
    alignItems: 'center',
  },
  metricValue: { ...type.section, color: colors.ink },
  metricLabel: { ...type.caption, color: colors.muted },
  errorBlock: {
    backgroundColor: colors.coralSoft,
    padding: 14,
    borderRadius: radius.medium,
  },
  errorDot: { backgroundColor: colors.coral },
  pressed: { opacity: 0.72, transform: [{ scale: 0.98 }] },
});

