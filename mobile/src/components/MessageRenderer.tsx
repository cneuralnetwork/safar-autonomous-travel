import { memo, useState } from 'react';
import {
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import { Image } from 'expo-image';
import { LinearGradient } from 'expo-linear-gradient';
import { Ionicons } from '@expo/vector-icons';
import type {
  Approval,
  ChatMessage,
  FlightLegOption,
  FlightOption,
  HotelOption,
  Itinerary,
  ItineraryItem,
  OperationEvent,
  PackageOption,
  SelectionKind,
  TaskGraph,
  TaskStatus,
  TravelConstraints,
  VisualTheme,
} from '@/types';
import { colors, radius, shadow, type } from '@/theme';
import { formatCurrency, formatDate, formatTime } from '@/utils/format';
import { ItineraryMap } from '@/components/ItineraryMap';
import { tripImageForTrip } from '@/lib/tripImages';

interface MessageRendererProps {
  message: ChatMessage;
  activeGraph?: TaskGraph;
  onApproval: (decision: 'approve' | 'edit' | 'cancel') => void;
  onCalendarExport: () => void;
  onSelectOption: (kind: SelectionKind, optionId: string) => void;
  approvalBusy: boolean;
  selectionBusy: boolean;
  selectedOutboundId?: string;
  selectedReturnId?: string;
  selectedHotelId?: string;
  destination?: string;
  visualTheme?: VisualTheme;
}

type IconName = keyof typeof Ionicons.glyphMap;

const STATUS_COLORS: Record<TaskStatus, string> = {
  waiting: colors.faint,
  running: colors.primary,
  completed: colors.green,
  retrying: colors.amber,
  failed: colors.coral,
  awaiting_input: colors.info,
  awaiting_approval: colors.info,
  skipped: colors.faint,
};

const CATEGORY_ICONS: Record<ItineraryItem['category'], IconName> = {
  flight: 'airplane',
  hotel: 'bed',
  activity: 'camera',
  transfer: 'car',
  meal: 'restaurant',
  buffer: 'time',
};

const CATEGORY_COLORS: Record<ItineraryItem['category'], string> = {
  flight: colors.primary,
  hotel: colors.info,
  activity: colors.green,
  transfer: colors.primaryLight,
  meal: colors.amber,
  buffer: colors.faint,
};

export const MessageRenderer = memo(function MessageRenderer({
  message,
  activeGraph,
  onApproval,
  onCalendarExport,
  onSelectOption,
  approvalBusy,
  selectionBusy,
  selectedOutboundId,
  selectedReturnId,
  selectedHotelId,
  destination,
  visualTheme,
}: MessageRendererProps) {
  if (message.role === 'user') {
    return (
      <View style={styles.userRow}>
        <View style={styles.userBubble}>
          <View style={styles.userSparkle}>
            <Ionicons name="sparkles" size={15} color={colors.primary} />
          </View>
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
          memoryApplied={Boolean(message.payload.memory_applied)}
        />
      );
    case 'clarification':
      return <ClarificationCard text={message.text} />;
    case 'task_graph':
      return (
        <TaskGraphCard
          graph={activeGraph || (message.payload.graph as TaskGraph)}
        />
      );
    case 'operation':
      return <OperationRow event={message.payload.event as OperationEvent} />;
    case 'flight_options':
      return <FlightCards flights={(message.payload.flights as FlightOption[]) || []} />;
    case 'flight_selection': {
      const selectionKind = message.payload.selection_kind as SelectionKind;
      return (
        <FlightSelectionCards
          flights={(message.payload.flights as FlightLegOption[]) || []}
          kind={selectionKind}
          selectedId={
            selectionKind === 'return_flight'
              ? selectedReturnId
              : selectedOutboundId
          }
          busy={selectionBusy}
          onSelect={onSelectOption}
        />
      );
    }
    case 'hotel_options':
      return (
        <HotelCards
          hotels={(message.payload.hotels as HotelOption[]) || []}
          destination={destination}
          visualTheme={visualTheme}
        />
      );
    case 'hotel_selection':
      return (
        <HotelCards
          hotels={(message.payload.hotels as HotelOption[]) || []}
          destination={destination}
          visualTheme={visualTheme}
          selectionKind="hotel"
          selectedId={selectedHotelId}
          busy={selectionBusy}
          onSelect={onSelectOption}
        />
      );
    case 'selection_confirmation':
      return <SelectionConfirmation text={message.text} />;
    case 'budget':
      return (
        <BudgetCard
          selected={message.payload.selected_package as PackageOption}
          rejectedCount={(message.payload.rejected_count as number) || 0}
        />
      );
    case 'itinerary':
      return (
        <ItineraryCard
          itinerary={message.payload.itinerary as Itinerary}
          destination={destination}
          visualTheme={visualTheme}
        />
      );
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
          busy={approvalBusy}
          onExport={onCalendarExport}
          destination={destination}
          visualTheme={visualTheme}
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
  trailing,
}: {
  icon: IconName;
  eyebrow: string;
  title: string;
  trailing?: string;
}) {
  return (
    <View style={styles.cardHeader}>
      <View style={styles.iconTile}>
        <Ionicons name={icon} size={18} color={colors.primary} />
      </View>
      <View style={styles.cardHeaderCopy}>
        <Text style={styles.eyebrow}>{eyebrow}</Text>
        <Text style={styles.cardTitle}>{title}</Text>
      </View>
      {trailing ? (
        <View style={styles.headerPill}>
          <Text style={styles.headerPillText}>{trailing}</Text>
        </View>
      ) : null}
    </View>
  );
}

function InterpretationCard({
  text,
  constraints,
  memoryApplied,
}: {
  text: string;
  constraints?: TravelConstraints;
  memoryApplied: boolean;
}) {
  if (!constraints) return <SystemCard text={text} />;

  const facts = [
    constraints.origin && constraints.destination
      ? {
          icon: 'navigate-outline' as const,
          label: 'Route',
          value: `${constraints.origin} → ${constraints.destination}`,
        }
      : null,
    constraints.duration_days
      ? {
          icon: 'calendar-outline' as const,
          label: 'Length',
          value: `${constraints.duration_days} day${constraints.duration_days === 1 ? '' : 's'}`,
        }
      : null,
    constraints.adults
      ? {
          icon: 'people-outline' as const,
          label: 'Travellers',
          value: `${constraints.adults + constraints.children}`,
        }
      : null,
    constraints.budget
      ? {
          icon: 'wallet-outline' as const,
          label: 'Budget',
          value: formatCurrency(constraints.budget),
        }
      : null,
  ].filter((fact): fact is NonNullable<typeof fact> => Boolean(fact));

  const preferences = [
    constraints.earliest_departure
      ? `Depart after ${constraints.earliest_departure.slice(0, 5)}`
      : null,
    constraints.hotel_area_preference
      ? `Stay near ${constraints.hotel_area_preference}`
      : null,
    ...(constraints.preferences || []),
  ].filter((preference): preference is string => Boolean(preference));

  return (
    <View style={[styles.card, styles.briefCard]}>
      <CardHeader icon="sparkles" eyebrow="Trip brief" title="Here’s what I understood" />
      <Text style={styles.body}>{text}</Text>
      {facts.length ? (
        <View style={styles.factGrid}>
          {facts.map((fact) => (
            <View key={fact.label} style={styles.fact}>
              <Ionicons name={fact.icon} size={16} color={colors.primary} />
              <Text style={styles.factLabel}>{fact.label}</Text>
              <Text style={styles.factValue} numberOfLines={1}>
                {fact.value}
              </Text>
            </View>
          ))}
        </View>
      ) : null}
      {preferences.length ? (
        <View style={styles.chipWrap}>
          {preferences.map((preference, index) => (
            <View key={`${preference}-${index}`} style={styles.chip}>
              <Ionicons name="checkmark" size={13} color={colors.primary} />
              <Text style={styles.chipText}>{preference}</Text>
            </View>
          ))}
        </View>
      ) : null}
      {memoryApplied ? (
        <View style={styles.memoryNotice}>
          <Ionicons name="time-outline" size={15} color={colors.primary} />
          <Text style={styles.memoryNoticeText}>Your saved preferences were applied</Text>
        </View>
      ) : null}
    </View>
  );
}

function ClarificationCard({ text }: { text: string }) {
  return (
    <View style={styles.assistantBlock}>
      <View style={styles.assistantMark}>
        <Ionicons name="navigate" size={15} color={colors.surface} />
      </View>
      <View style={styles.assistantCopy}>
        <Text style={styles.assistantLabel}>Safar needs one detail</Text>
        <Text style={styles.assistantText}>{text}</Text>
      </View>
    </View>
  );
}

function TaskGraphCard({ graph }: { graph?: TaskGraph }) {
  if (!graph) return null;

  const journeyStepDefinitions: Array<{
    id: string;
    title: string;
    description: string;
    icon: IconName;
    taskIds: string[];
  }> = [
    {
      id: 'brief',
      title: 'Trip details understood',
      description: 'Route, dates, budget and preferences',
      icon: 'sparkles',
      taskIds: ['understand_request', 'resolve_constraints', 'resolve_locations'],
    },
    {
      id: 'outbound',
      title: 'Choose your flight there',
      description: 'Live options for your departure day',
      icon: 'airplane-outline',
      taskIds: [
        'outbound_flight_search',
        'choose_outbound_flight',
      ],
    },
    {
      id: 'return',
      title: 'Choose your flight back',
      description: 'A separate search for the return day',
      icon: 'return-down-back-outline',
      taskIds: ['return_flight_search', 'choose_return_flight'],
    },
    {
      id: 'stay',
      title: 'Choose your stay',
      description: 'Hotels that fit the trip and budget',
      icon: 'bed-outline',
      taskIds: ['hotel_search', 'choose_hotel'],
    },
    {
      id: 'itinerary',
      title: 'Build your day-by-day plan',
      description: 'Validate the total and arrange the route',
      icon: 'map-outline',
      taskIds: ['place_search', 'compare_packages', 'create_itinerary'],
    },
    {
      id: 'review',
      title: 'Review your trip',
      description: 'Nothing is saved without your approval',
      icon: 'checkmark-circle-outline',
      taskIds: ['request_approval'],
    },
    {
      id: 'calendar',
      title: 'Download calendar file',
      description: 'Works with any calendar app',
      icon: 'download-outline',
      taskIds: ['add_calendar'],
    },
  ];
  const journeySteps = journeyStepDefinitions.map((step) => {
    const tasks = graph.tasks.filter((task) => step.taskIds.includes(task.id));
    const status: TaskStatus = tasks.some((task) => task.status === 'failed')
      ? 'failed'
      : tasks.some((task) => task.status === 'awaiting_input')
        ? 'awaiting_input'
        : tasks.some((task) => task.status === 'awaiting_approval')
          ? 'awaiting_approval'
          : tasks.some((task) =>
                ['running', 'retrying'].includes(task.status),
              ) ||
              (tasks.some((task) =>
                ['completed', 'skipped'].includes(task.status),
              ) &&
                !tasks.every((task) =>
                  ['completed', 'skipped'].includes(task.status),
                ))
            ? 'running'
            : tasks.length > 0 &&
                tasks.every((task) =>
                  ['completed', 'skipped'].includes(task.status),
                )
              ? 'completed'
              : 'waiting';
    return { ...step, status };
  });
  const completed = journeySteps.filter(
    (step) => step.status === 'completed',
  ).length;
  const progress = completed / journeySteps.length;
  const needsReview = journeySteps.some(
    (step) => step.status === 'awaiting_approval',
  );
  const needsChoice = journeySteps.some(
    (step) => step.status === 'awaiting_input',
  );
  const statusLabel: Record<TaskStatus, string> = {
    completed: 'Ready',
    running: 'Working on this',
    retrying: 'Trying another option',
    awaiting_input: 'Waiting for you',
    awaiting_approval: 'Ready for you',
    failed: 'Needs attention',
    waiting: 'Up next',
    skipped: 'Not needed',
  };

  return (
    <View style={styles.card}>
      <CardHeader
        icon="sparkles"
        eyebrow="Trip progress"
        title={graph.goal}
        trailing={
          needsChoice
            ? 'Your choice'
            : needsReview
              ? 'Your review'
              : completed === journeySteps.length
                ? 'Ready'
                : 'Planning'
        }
      />
      <View style={styles.progressTrack}>
        <View style={[styles.progressFill, { width: `${Math.round(progress * 100)}%` }]} />
      </View>
      <View style={styles.journeyProgress}>
        {journeySteps.map((step) => (
          <View key={step.id} style={styles.journeyStep}>
            <View
              style={[
                styles.journeyStepIcon,
                step.status === 'completed' && styles.journeyStepIconComplete,
                ['awaiting_input', 'awaiting_approval'].includes(step.status) &&
                  styles.journeyStepIconReview,
                step.status === 'failed' && styles.journeyStepIconFailed,
              ]}
            >
              <Ionicons
                name={step.status === 'completed' ? 'checkmark' : step.icon}
                size={16}
                color={
                  step.status === 'completed'
                    ? colors.surface
                    : step.status === 'failed'
                      ? colors.coral
                      : colors.primary
                }
              />
            </View>
            <View style={styles.journeyStepCopy}>
              <Text style={styles.journeyStepTitle}>{step.title}</Text>
              <Text style={styles.journeyStepDescription}>
                {step.description}
              </Text>
            </View>
            <Text
              style={[
                styles.journeyStepStatus,
                step.status === 'completed' && styles.journeyStepStatusComplete,
                ['awaiting_input', 'awaiting_approval'].includes(step.status) &&
                  styles.journeyStepStatusReview,
                step.status === 'failed' && styles.journeyStepStatusFailed,
              ]}
            >
              {statusLabel[step.status]}
            </Text>
          </View>
        ))}
      </View>
    </View>
  );
}

function OperationRow({ event }: { event?: OperationEvent }) {
  if (!event) return null;
  const icon: IconName =
    event.status === 'completed'
      ? 'checkmark'
      : event.status === 'retrying'
        ? 'refresh'
        : event.status === 'failed'
          ? 'alert'
          : event.status === 'awaiting_approval'
            ? 'lock-closed'
            : 'ellipsis-horizontal';

  return (
    <View style={styles.operation}>
      <View
        style={[
          styles.operationIcon,
          { backgroundColor: STATUS_COLORS[event.status] },
        ]}
      >
        <Ionicons name={icon} size={12} color={colors.surface} />
      </View>
      <View style={styles.operationCopy}>
        <Text style={styles.operationSummary}>{event.summary}</Text>
        {event.reason ? <Text style={styles.operationReason}>{event.reason}</Text> : null}
      </View>
    </View>
  );
}

function FlightCards({ flights }: { flights: FlightOption[] }) {
  if (!flights.length) return null;

  return (
    <View style={styles.collection}>
      <View style={styles.sectionHeading}>
        <View>
          <Text style={styles.sectionEyebrow}>Compared live options</Text>
          <Text style={styles.sectionLabel}>Flights</Text>
        </View>
        <Text style={styles.sectionCount}>{flights.length} found</Text>
      </View>
      <ScrollView
        horizontal
        showsHorizontalScrollIndicator={false}
        contentContainerStyle={styles.carousel}
      >
        {flights.slice(0, 5).map((flight, index) => {
          const outbound = flight.outbound[0];
          const arrival = flight.outbound.at(-1);
          if (!outbound || !arrival) return null;
          const durationMinutes = flight.outbound.reduce(
            (total, segment) => total + segment.duration_minutes,
            0,
          );
          const duration = `${Math.floor(durationMinutes / 60)}h ${
            durationMinutes % 60
          }m`;
          return (
            <View key={flight.id} style={styles.optionCard}>
              <View style={styles.optionTop}>
                <View style={styles.airlineBadge}>
                  <Ionicons name="airplane" size={16} color={colors.primary} />
                </View>
                <View style={styles.optionHeading}>
                  <Text style={styles.optionRank}>
                    {index === 0 ? 'Best match' : `Option ${index + 1}`}
                  </Text>
                  <Text style={styles.optionAirline} numberOfLines={1}>
                    {outbound.airline}
                  </Text>
                </View>
                <Text style={styles.optionPrice}>{formatCurrency(flight.total_price)}</Text>
              </View>
              <View style={styles.routeRow}>
                <View style={styles.routeEnd}>
                  <Text style={styles.airport}>{outbound.departure_airport}</Text>
                  <Text style={styles.mini}>{formatTime(outbound.departure_at)}</Text>
                </View>
                <View style={styles.routeLine}>
                  <View style={styles.routeDot} />
                  <View style={styles.routeStroke} />
                  <Ionicons name="airplane" size={13} color={colors.primary} />
                  <View style={styles.routeStroke} />
                  <View style={styles.routeDot} />
                </View>
                <View style={[styles.routeEnd, styles.routeArrival]}>
                  <Text style={styles.airport}>{arrival.arrival_airport}</Text>
                  <Text style={styles.mini}>{formatTime(arrival.arrival_at)}</Text>
                </View>
              </View>
              <View style={styles.flightFacts}>
                <View style={styles.flightFact}>
                  <Text style={styles.flightFactValue}>
                    {flight.stops ? `${flight.stops} stop${flight.stops === 1 ? '' : 's'}` : 'Nonstop'}
                  </Text>
                  <Text style={styles.flightFactLabel}>Journey</Text>
                </View>
                <View style={styles.flightFactDivider} />
                <View style={styles.flightFact}>
                  <Text style={styles.flightFactValue}>{duration}</Text>
                  <Text style={styles.flightFactLabel}>Flight time</Text>
                </View>
                {flight.baggage ? (
                  <>
                    <View style={styles.flightFactDivider} />
                    <View style={styles.flightFact}>
                      <Text style={styles.flightFactValue} numberOfLines={1}>
                        {flight.baggage}
                      </Text>
                      <Text style={styles.flightFactLabel}>Baggage</Text>
                    </View>
                  </>
                ) : null}
              </View>
            </View>
          );
        })}
      </ScrollView>
    </View>
  );
}

function FlightSelectionCards({
  flights,
  kind,
  selectedId,
  busy,
  onSelect,
}: {
  flights: FlightLegOption[];
  kind: SelectionKind;
  selectedId?: string;
  busy: boolean;
  onSelect: (kind: SelectionKind, optionId: string) => void;
}) {
  if (!flights.length) return null;
  const outbound = kind === 'outbound_flight';

  return (
    <View style={styles.collection}>
      <View style={styles.sectionHeading}>
        <View>
          <Text style={styles.sectionEyebrow}>
            {outbound ? 'Step 1 · flight there' : 'Step 2 · flight back'}
          </Text>
          <Text style={styles.sectionLabel}>
            {outbound ? 'Choose your outbound' : 'Choose your return'}
          </Text>
        </View>
        <Text style={styles.sectionCount}>{flights.length} choices</Text>
      </View>
      <ScrollView
        horizontal
        showsHorizontalScrollIndicator={false}
        contentContainerStyle={styles.carousel}
      >
        {flights.slice(0, 6).map((flight, index) => {
          const first = flight.segments[0];
          const last = flight.segments.at(-1);
          if (!first || !last) return null;
          const isSelected = selectedId === flight.id;
          const durationMinutes = flight.segments.reduce(
            (total, segment) => total + segment.duration_minutes,
            0,
          );
          const duration = `${Math.floor(durationMinutes / 60)}h ${
            durationMinutes % 60
          }m`;
          return (
            <Pressable
              key={flight.id}
              onPress={() => onSelect(kind, flight.id)}
              disabled={busy || isSelected}
              accessibilityRole="radio"
              accessibilityState={{ selected: isSelected, disabled: busy }}
              accessibilityLabel={`Option ${index + 1}, ${first.airline}, ${formatCurrency(flight.total_price)}`}
              style={({ pressed }) => [
                styles.optionCard,
                isSelected && styles.selectionCardSelected,
                pressed && styles.pressed,
              ]}
            >
              <View style={styles.optionTop}>
                <View style={styles.airlineBadge}>
                  <Ionicons name="airplane" size={16} color={colors.primary} />
                </View>
                <View style={styles.optionHeading}>
                  <Text style={styles.optionRank}>Option {index + 1}</Text>
                  <Text style={styles.optionAirline} numberOfLines={1}>
                    {first.airline}
                  </Text>
                </View>
                <View style={styles.legPriceWrap}>
                  <Text style={styles.optionPrice}>
                    {formatCurrency(flight.total_price)}
                  </Text>
                  <Text style={styles.legPriceLabel}>this leg</Text>
                </View>
              </View>
              <View style={styles.routeRow}>
                <View style={styles.routeEnd}>
                  <Text style={styles.airport}>{first.departure_airport}</Text>
                  <Text style={styles.mini}>{formatTime(first.departure_at)}</Text>
                </View>
                <View style={styles.routeLine}>
                  <View style={styles.routeDot} />
                  <View style={styles.routeStroke} />
                  <Ionicons name="airplane" size={13} color={colors.primary} />
                  <View style={styles.routeStroke} />
                  <View style={styles.routeDot} />
                </View>
                <View style={[styles.routeEnd, styles.routeArrival]}>
                  <Text style={styles.airport}>{last.arrival_airport}</Text>
                  <Text style={styles.mini}>{formatTime(last.arrival_at)}</Text>
                </View>
              </View>
              <View style={styles.flightFacts}>
                <View style={styles.flightFact}>
                  <Text style={styles.flightFactValue}>
                    {flight.stops
                      ? `${flight.stops} stop${flight.stops === 1 ? '' : 's'}`
                      : 'Nonstop'}
                  </Text>
                  <Text style={styles.flightFactLabel}>Journey</Text>
                </View>
                <View style={styles.flightFactDivider} />
                <View style={styles.flightFact}>
                  <Text style={styles.flightFactValue}>{duration}</Text>
                  <Text style={styles.flightFactLabel}>Flight time</Text>
                </View>
                <View style={styles.flightFactDivider} />
                <View style={styles.flightFact}>
                  <Text style={styles.flightFactValue} numberOfLines={1}>
                    {first.flight_number || 'Live fare'}
                  </Text>
                  <Text style={styles.flightFactLabel}>Flight</Text>
                </View>
              </View>
              <View
                style={[
                  styles.selectionAction,
                  isSelected && styles.selectionActionSelected,
                ]}
              >
                <Ionicons
                  name={isSelected ? 'checkmark-circle' : 'add-circle-outline'}
                  size={17}
                  color={isSelected ? colors.surface : colors.primary}
                />
                <Text
                  style={[
                    styles.selectionActionText,
                    isSelected && styles.selectionActionTextSelected,
                  ]}
                >
                  {isSelected ? 'Selected' : `Choose option ${index + 1}`}
                </Text>
              </View>
            </Pressable>
          );
        })}
      </ScrollView>
      <Text style={styles.selectionHint}>
        Prefer chat? Say “choose option 2” or name the airline.
      </Text>
    </View>
  );
}

function HotelCards({
  hotels,
  destination,
  visualTheme,
  selectionKind,
  selectedId,
  busy = false,
  onSelect,
}: {
  hotels: HotelOption[];
  destination?: string;
  visualTheme?: VisualTheme;
  selectionKind?: SelectionKind;
  selectedId?: string;
  busy?: boolean;
  onSelect?: (kind: SelectionKind, optionId: string) => void;
}) {
  if (!hotels.length) return null;

  return (
    <View style={styles.collection}>
      <View style={styles.sectionHeading}>
        <View>
          <Text style={styles.sectionEyebrow}>Handpicked for the route</Text>
          <Text style={styles.sectionLabel}>Stays</Text>
        </View>
        <Text style={styles.sectionCount}>{hotels.length} found</Text>
      </View>
      <ScrollView
        horizontal
        showsHorizontalScrollIndicator={false}
        contentContainerStyle={styles.carousel}
      >
        {hotels.slice(0, 5).map((hotel, index) => {
          const isSelected = selectedId === hotel.id;
          const fallback = tripImageForTrip({
            key: hotel.id || `${hotel.name}:${hotel.address}`,
            destination,
            visualTheme,
          });
          return (
            <Pressable
              key={hotel.id}
              disabled={!selectionKind || !onSelect || busy || isSelected}
              onPress={() => {
                if (selectionKind && onSelect) onSelect(selectionKind, hotel.id);
              }}
              accessibilityRole={selectionKind ? 'radio' : undefined}
              accessibilityState={
                selectionKind ? { selected: isSelected, disabled: busy } : undefined
              }
              style={({ pressed }) => [
                styles.hotelCard,
                isSelected && styles.selectionCardSelected,
                pressed && styles.pressed,
              ]}
            >
              <View style={styles.hotelImageFrame}>
                <Image
                  source={hotel.image_url ? { uri: hotel.image_url } : fallback}
                  placeholder={fallback}
                  style={styles.hotelImage}
                  contentFit="cover"
                  transition={180}
                />
                {index === 0 ? (
                  <View style={styles.bestPill}>
                    <Ionicons name="sparkles" size={12} color={colors.primary} />
                    <Text style={styles.bestPillText}>Best value</Text>
                  </View>
                ) : null}
                <View style={styles.ratingPill}>
                  <Ionicons name="star" size={11} color={colors.amber} />
                  <Text style={styles.ratingPillText}>{hotel.rating || 'New'}</Text>
                </View>
              </View>
              <View style={styles.hotelCopy}>
                <Text style={styles.hotelName} numberOfLines={1}>
                  {hotel.name}
                </Text>
                <Text style={styles.hotelAddress} numberOfLines={1}>
                  {hotel.address}
                </Text>
                <View style={styles.hotelBottom}>
                  <View>
                    <Text style={styles.hotelPrice}>{formatCurrency(hotel.total_price)}</Text>
                    <Text style={styles.hotelTotal}>total stay</Text>
                  </View>
                  {hotel.distance_to_preference_km != null ? (
                    <View style={styles.distancePill}>
                      <Ionicons name="location-outline" size={12} color={colors.primary} />
                      <Text style={styles.distanceText}>
                        {hotel.distance_to_preference_km} km
                      </Text>
                    </View>
                  ) : null}
                </View>
                {selectionKind ? (
                  <View
                    style={[
                      styles.selectionAction,
                      isSelected && styles.selectionActionSelected,
                    ]}
                  >
                    <Ionicons
                      name={isSelected ? 'checkmark-circle' : 'bed-outline'}
                      size={17}
                      color={isSelected ? colors.surface : colors.primary}
                    />
                    <Text
                      style={[
                        styles.selectionActionText,
                        isSelected && styles.selectionActionTextSelected,
                      ]}
                    >
                      {isSelected ? 'Selected' : `Choose stay ${index + 1}`}
                    </Text>
                  </View>
                ) : null}
              </View>
            </Pressable>
          );
        })}
      </ScrollView>
      {selectionKind ? (
        <Text style={styles.selectionHint}>
          You can also say “pick the second stay” or type the hotel name.
        </Text>
      ) : null}
    </View>
  );
}

function SelectionConfirmation({ text }: { text: string }) {
  return (
    <View style={styles.selectionConfirmation}>
      <View style={styles.selectionConfirmationIcon}>
        <Ionicons name="checkmark" size={15} color={colors.surface} />
      </View>
      <Text style={styles.selectionConfirmationText}>{text}</Text>
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

  const inclusions: Array<{
    icon: IconName;
    label: string;
    value: string;
  }> = [
    {
      icon: 'airplane-outline',
      label: 'Flights',
      value: formatCurrency(selected.flight.total_price),
    },
    {
      icon: 'bed-outline',
      label: 'Stay',
      value: formatCurrency(selected.hotel.total_price),
    },
    {
      icon: 'car-outline',
      label: 'Transfers',
      value: formatCurrency(selected.local_transfer_reserve),
    },
    {
      icon: 'compass-outline',
      label: 'Trip fund',
      value: formatCurrency(selected.on_trip_reserve),
    },
  ];

  const stats = [
    {
      label: 'Remaining',
      value:
        selected.remaining_budget != null
          ? formatCurrency(selected.remaining_budget)
          : 'No budget cap',
      helper: 'in your budget',
    },
    {
      label: 'Stay rating',
      value: selected.hotel.rating ? `${selected.hotel.rating}/5` : 'New',
      helper: selected.hotel.name,
    },
    {
      label: 'Plan score',
      value: `${Math.round(selected.score)}%`,
      helper: 'overall match',
    },
  ];

  return (
    <View style={[styles.card, styles.planCard]}>
      <CardHeader
        icon="sparkles"
        eyebrow="Best plan found"
        title="A balanced trip within your limits"
        trailing={`${Math.round(selected.score)}% match`}
      />
      <View style={styles.planSummary}>
        <View>
          <Text style={styles.planTotal}>{formatCurrency(selected.total_price)}</Text>
          <Text style={styles.planTotalLabel}>estimated trip total</Text>
        </View>
        <View style={styles.planShield}>
          <Ionicons name="shield-checkmark" size={21} color={colors.primary} />
        </View>
      </View>
      <View style={styles.statGrid}>
        {stats.map((stat) => (
          <View key={stat.label} style={styles.statCard}>
            <Text style={styles.statLabel}>{stat.label}</Text>
            <Text style={styles.statValue} numberOfLines={1}>
              {stat.value}
            </Text>
            <Text style={styles.statHelper} numberOfLines={1}>
              {stat.helper}
            </Text>
          </View>
        ))}
      </View>
      <View style={styles.inclusionSection}>
        <Text style={styles.inclusionTitle}>Package breakdown</Text>
        <View style={styles.inclusionGrid}>
          {inclusions.map((inclusion) => (
            <View key={inclusion.label} style={styles.inclusion}>
              <View style={styles.inclusionIcon}>
                <Ionicons name={inclusion.icon} size={17} color={colors.primary} />
              </View>
              <Text style={styles.inclusionLabel}>{inclusion.label}</Text>
              <Text style={styles.inclusionValue} numberOfLines={1}>
                {inclusion.value}
              </Text>
            </View>
          ))}
        </View>
      </View>
      {rejectedCount > 0 ? (
        <View style={styles.rejectedLine}>
          <Ionicons name="checkmark-circle" size={16} color={colors.green} />
          <Text style={styles.rejectedText}>
            {rejectedCount} option{rejectedCount === 1 ? '' : 's'} removed for breaking your constraints
          </Text>
        </View>
      ) : null}
    </View>
  );
}

function ItineraryCard({
  itinerary,
  destination,
  visualTheme,
}: {
  itinerary?: Itinerary;
  destination?: string;
  visualTheme?: VisualTheme;
}) {
  const [activeDay, setActiveDay] = useState(0);
  if (!itinerary?.days.length) return null;

  const safeIndex = Math.min(activeDay, itinerary.days.length - 1);
  const selectedDay = itinerary.days[safeIndex];
  const firstDay = itinerary.days[0];
  const lastDay = itinerary.days.at(-1);
  if (!selectedDay || !firstDay || !lastDay) return null;

  const heroSource = tripImageForTrip({
    key: `${selectedDay.date}:${selectedDay.title}`,
    destination,
    visualTheme,
  });

  return (
    <View style={[styles.card, styles.itineraryCard]}>
      <CardHeader
        icon="map-outline"
        eyebrow={`${itinerary.days.length}-day itinerary`}
        title={`${formatDate(firstDay.date)} – ${formatDate(lastDay.date)}`}
        trailing={`${selectedDay.items.length} stops`}
      />
      <ScrollView
        horizontal
        showsHorizontalScrollIndicator={false}
        contentContainerStyle={styles.dayTabs}
      >
        {itinerary.days.map((day, index) => {
          const selected = index === safeIndex;
          return (
            <Pressable
              key={day.date}
              accessibilityRole="tab"
              accessibilityState={{ selected }}
              onPress={() => setActiveDay(index)}
              style={({ pressed }) => [
                styles.dayTab,
                selected && styles.dayTabActive,
                pressed && styles.pressed,
              ]}
            >
              <Text style={[styles.dayTabLabel, selected && styles.dayTabLabelActive]}>
                Day {index + 1}
              </Text>
              <Text style={[styles.dayTabDate, selected && styles.dayTabDateActive]}>
                {formatDate(day.date)}
              </Text>
            </Pressable>
          );
        })}
      </ScrollView>
      <View style={styles.itineraryHero}>
        <Image source={heroSource} style={StyleSheet.absoluteFill} contentFit="cover" />
        <LinearGradient
          colors={[`${colors.navyDeep}00`, `${colors.navyDeep}F5`]}
          style={StyleSheet.absoluteFill}
        />
        <View style={styles.itineraryHeroCopy}>
          <Text style={styles.itineraryHeroEyebrow}>
            Day {safeIndex + 1} · {formatDate(selectedDay.date)}
          </Text>
          <Text style={styles.itineraryHeroTitle}>{selectedDay.title}</Text>
        </View>
      </View>
      <ItineraryMap itinerary={itinerary} />
      <View style={styles.timelineCard}>
        <View style={styles.timelineHeading}>
          <View>
            <Text style={styles.timelineEyebrow}>Your day at a glance</Text>
            <Text style={styles.timelineTitle}>{selectedDay.title}</Text>
          </View>
          <View style={styles.timelineCount}>
            <Text style={styles.timelineCountText}>{selectedDay.items.length}</Text>
          </View>
        </View>
        <View style={styles.timeline}>
          {selectedDay.items.map((item, index) => (
            <View key={item.id} style={styles.timelineRow}>
              <View style={styles.timelineTimeColumn}>
                <Text style={styles.itemTime}>{formatTime(item.start_at)}</Text>
              </View>
              <View style={styles.timelineRail}>
                {index < selectedDay.items.length - 1 ? (
                  <View style={styles.timelineLine} />
                ) : null}
                <View
                  style={[
                    styles.timelineDot,
                    { backgroundColor: CATEGORY_COLORS[item.category] },
                  ]}
                >
                  <Ionicons
                    name={CATEGORY_ICONS[item.category]}
                    size={11}
                    color={colors.surface}
                  />
                </View>
              </View>
              <View style={styles.timelineCopy}>
                <Text style={styles.itemTitle}>{item.title}</Text>
                {item.location ? (
                  <Text style={styles.itemLocation} numberOfLines={1}>
                    {item.location}
                  </Text>
                ) : null}
                {item.description ? (
                  <Text style={styles.itemDescription} numberOfLines={2}>
                    {item.description}
                  </Text>
                ) : null}
              </View>
            </View>
          ))}
        </View>
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
      <View style={styles.approvalTop}>
        <View style={styles.approvalIcon}>
          <Ionicons name="document-attach" size={22} color={colors.primary} />
        </View>
        <View style={styles.approvalCopy}>
          <Text style={styles.approvalEyebrow}>Take your plan anywhere</Text>
          <Text style={styles.approvalTitle}>Download a calendar file</Text>
        </View>
      </View>
      <View style={styles.approvalStats}>
        <View style={styles.approvalStat}>
          <Text style={styles.approvalStatValue}>{approval.event_count}</Text>
          <Text style={styles.approvalStatLabel}>events</Text>
        </View>
        <View style={styles.approvalStatDivider} />
        <View style={styles.approvalStat}>
          <Text style={styles.approvalStatValue}>
            {formatCurrency(approval.estimated_trip_total)}
          </Text>
          <Text style={styles.approvalStatLabel}>trip estimate</Text>
        </View>
      </View>
      <View style={styles.safetyRow}>
        <Ionicons name="lock-closed" size={14} color={colors.green} />
        <Text style={styles.safetyText}>
          {approval.disclaimer} No calendar account access is needed.
        </Text>
      </View>
      <View style={styles.approvalActions}>
        <Pressable
          accessibilityRole="button"
          onPress={() => onDecision('edit')}
          disabled={busy}
          style={({ pressed }) => [styles.secondary, pressed && styles.pressed]}
        >
          <Ionicons name="options-outline" size={16} color={colors.primary} />
          <Text style={styles.secondaryText}>Customize</Text>
        </Pressable>
        <Pressable
          accessibilityRole="button"
          onPress={() => onDecision('approve')}
          disabled={busy}
          style={({ pressed }) => [styles.approve, pressed && styles.pressed]}
        >
          <Ionicons name="download-outline" size={16} color={colors.surface} />
          <Text style={styles.approveText}>
            {busy ? 'Preparing…' : 'Download .ics'}
          </Text>
        </Pressable>
      </View>
      <Pressable
        accessibilityRole="button"
        accessibilityLabel="Skip calendar download"
        onPress={() => onDecision('cancel')}
        disabled={busy}
        style={({ pressed }) => [styles.cancel, pressed && styles.pressed]}
      >
        <Text style={styles.cancelText}>Not now</Text>
      </Pressable>
    </View>
  );
}

function CalendarResult({
  text,
  busy,
  onExport,
  destination,
  visualTheme,
}: {
  text: string;
  busy: boolean;
  onExport: () => void;
  destination?: string;
  visualTheme?: VisualTheme;
}) {
  const heroSource = tripImageForTrip({
    key: 'calendar-export',
    destination,
    visualTheme,
  });
  return (
    <View style={[styles.card, styles.calendarCard]}>
      <View style={styles.calendarHero}>
        <Image
          source={heroSource}
          style={StyleSheet.absoluteFill}
          contentFit="cover"
        />
        <View style={styles.calendarTint} />
        <View style={styles.calendarCheck}>
          <Ionicons name="checkmark" size={20} color={colors.surface} />
        </View>
      </View>
      <View style={styles.successTitleRow}>
        <View style={styles.successCopy}>
          <Text style={styles.successEyebrow}>Trip saved</Text>
          <Text style={styles.cardTitle}>{text}</Text>
        </View>
        <View style={styles.successPill}>
          <Ionicons name="document-outline" size={14} color={colors.primary} />
          <Text style={styles.successPillText}>.ics</Text>
        </View>
      </View>
      <Text style={styles.body}>
        Open this portable file with whichever calendar app you already use.
      </Text>
      <Pressable
        accessibilityRole="button"
        accessibilityLabel="Download itinerary calendar file"
        disabled={busy}
        onPress={onExport}
        style={({ pressed }) => [
          styles.calendarDownload,
          pressed && styles.pressed,
        ]}
      >
        {busy ? (
          <Text style={styles.approveText}>Preparing…</Text>
        ) : (
          <>
            <Ionicons name="download-outline" size={17} color={colors.surface} />
            <Text style={styles.approveText}>Download again</Text>
          </>
        )}
      </Pressable>
    </View>
  );
}

function ReportCard({ report }: { report?: Record<string, unknown> }) {
  if (!report) return null;

  const selected = report.selected_package as PackageOption | undefined;
  const itinerary = report.itinerary as Itinerary | undefined;
  const metrics = [
    {
      icon: 'wallet-outline' as const,
      label: 'Trip total',
      value: formatCurrency(selected?.total_price || 0),
    },
    {
      icon: 'calendar-clear-outline' as const,
      label: 'Days',
      value: String(itinerary?.days.length || 0),
    },
    {
      icon: 'pricetag-outline' as const,
      label: 'Savings',
      value: formatCurrency(Number(report.estimated_savings || 0)),
    },
    {
      icon: 'calendar-outline' as const,
      label: 'Calendar',
      value: 'ICS ready',
    },
  ];

  return (
    <View style={styles.card}>
      <CardHeader
        icon="checkmark-circle-outline"
        eyebrow="Trip summary"
        title="Your plan is ready"
      />
      <View style={styles.metricGrid}>
        {metrics.map((metric) => (
          <View key={metric.label} style={styles.metric}>
            <View style={styles.metricIcon}>
              <Ionicons name={metric.icon} size={16} color={colors.primary} />
            </View>
            <Text style={styles.metricValue} numberOfLines={1}>
              {metric.value}
            </Text>
            <Text style={styles.metricLabel}>{metric.label}</Text>
          </View>
        ))}
      </View>
      <View style={styles.reportNotice}>
        <Ionicons name="shield-checkmark-outline" size={17} color={colors.green} />
        <Text style={styles.reportNoticeText}>
          Your choices and itinerary are saved here. No booking or payment was
          made.
        </Text>
      </View>
    </View>
  );
}

function SystemCard({ text, tone }: { text: string; tone?: 'error' }) {
  return (
    <View style={[styles.assistantBlock, tone === 'error' && styles.errorBlock]}>
      <View style={[styles.assistantMark, tone === 'error' && styles.errorMark]}>
        <Ionicons
          name={tone === 'error' ? 'alert' : 'navigate'}
          size={14}
          color={colors.surface}
        />
      </View>
      <View style={styles.systemCopy}>
        <Text style={[styles.assistantLabel, tone === 'error' && styles.errorLabel]}>
          {tone === 'error' ? 'Something needs attention' : 'Safar'}
        </Text>
        <Text style={styles.assistantText}>{text}</Text>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  userRow: {
    alignItems: 'flex-end',
    marginVertical: 7,
  },
  userBubble: {
    width: '88%',
    minHeight: 58,
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: 11,
    backgroundColor: colors.surface,
    borderRadius: radius.large,
    borderBottomRightRadius: 7,
    borderWidth: 1,
    borderColor: colors.surfaceViolet,
    paddingHorizontal: 15,
    paddingVertical: 14,
    ...shadow,
  },
  userSparkle: {
    width: 29,
    height: 29,
    borderRadius: 10,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: colors.primarySoft,
  },
  userText: {
    ...type.body,
    color: colors.ink,
    flex: 1,
    paddingTop: 3,
  },
  card: {
    marginVertical: 7,
    padding: 16,
    borderRadius: radius.large,
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.line,
    gap: 14,
    ...shadow,
  },
  briefCard: {
    backgroundColor: colors.surfaceTint,
    borderColor: colors.surfaceViolet,
  },
  cardHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 11,
  },
  iconTile: {
    width: 39,
    height: 39,
    borderRadius: 13,
    backgroundColor: colors.primarySoft,
    alignItems: 'center',
    justifyContent: 'center',
  },
  cardHeaderCopy: {
    flex: 1,
    gap: 1,
  },
  eyebrow: {
    ...type.caption,
    color: colors.primary,
    textTransform: 'uppercase',
    letterSpacing: 0.75,
  },
  cardTitle: {
    ...type.section,
    color: colors.ink,
  },
  headerPill: {
    minHeight: 25,
    borderRadius: radius.pill,
    paddingHorizontal: 9,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: colors.primarySoft,
  },
  headerPillText: {
    ...type.caption,
    color: colors.primary,
  },
  body: {
    ...type.body,
    color: colors.muted,
  },
  factGrid: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 7,
  },
  fact: {
    width: '48%',
    minHeight: 72,
    borderRadius: radius.medium,
    padding: 10,
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.line,
  },
  factLabel: {
    ...type.caption,
    color: colors.faint,
    marginTop: 5,
  },
  factValue: {
    ...type.label,
    color: colors.ink,
  },
  chipWrap: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 7,
  },
  chip: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
    paddingHorizontal: 10,
    paddingVertical: 7,
    borderRadius: radius.pill,
    backgroundColor: colors.primarySoft,
  },
  chipText: {
    ...type.caption,
    color: colors.primary,
  },
  memoryNotice: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 7,
    borderTopWidth: 1,
    borderTopColor: colors.line,
    paddingTop: 11,
  },
  memoryNoticeText: {
    ...type.caption,
    color: colors.primary,
  },
  assistantBlock: {
    marginVertical: 7,
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: 10,
    paddingRight: 8,
  },
  assistantMark: {
    width: 31,
    height: 31,
    borderRadius: 11,
    backgroundColor: colors.primary,
    alignItems: 'center',
    justifyContent: 'center',
    marginTop: 1,
  },
  assistantCopy: {
    flex: 1,
    gap: 7,
  },
  assistantLabel: {
    ...type.caption,
    color: colors.primary,
    textTransform: 'uppercase',
    letterSpacing: 0.7,
  },
  assistantText: {
    ...type.body,
    color: colors.ink,
    flex: 1,
  },
  progressTrack: {
    height: 5,
    borderRadius: radius.pill,
    backgroundColor: colors.primarySoft,
    overflow: 'hidden',
  },
  progressFill: {
    height: '100%',
    borderRadius: radius.pill,
    backgroundColor: colors.primary,
  },
  journeyProgress: {
    gap: 4,
  },
  journeyStep: {
    minHeight: 58,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 11,
    paddingVertical: 5,
  },
  journeyStepIcon: {
    width: 36,
    height: 36,
    borderRadius: 12,
    borderCurve: 'continuous',
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: colors.primarySoft,
  },
  journeyStepIconComplete: {
    backgroundColor: colors.green,
  },
  journeyStepIconReview: {
    backgroundColor: colors.infoSoft,
  },
  journeyStepIconFailed: {
    backgroundColor: colors.coralSoft,
  },
  journeyStepCopy: {
    flex: 1,
    minWidth: 0,
  },
  journeyStepTitle: {
    ...type.label,
    color: colors.ink,
  },
  journeyStepDescription: {
    ...type.caption,
    color: colors.muted,
  },
  journeyStepStatus: {
    ...type.caption,
    color: colors.faint,
    maxWidth: 72,
    textAlign: 'right',
  },
  journeyStepStatusComplete: {
    color: colors.green,
  },
  journeyStepStatusReview: {
    color: colors.info,
  },
  journeyStepStatusFailed: {
    color: colors.coral,
  },
  operation: {
    marginVertical: 3,
    marginLeft: 9,
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: 10,
    paddingVertical: 3,
  },
  operationIcon: {
    width: 23,
    height: 23,
    borderRadius: 8,
    alignItems: 'center',
    justifyContent: 'center',
  },
  operationCopy: {
    flex: 1,
    paddingTop: 1,
  },
  operationSummary: {
    ...type.label,
    color: colors.ink,
  },
  operationReason: {
    ...type.caption,
    color: colors.muted,
    marginTop: 2,
  },
  collection: {
    marginVertical: 7,
  },
  sectionHeading: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'flex-end',
    marginBottom: 10,
  },
  sectionEyebrow: {
    ...type.caption,
    color: colors.primary,
    textTransform: 'uppercase',
    letterSpacing: 0.7,
  },
  sectionLabel: {
    ...type.title,
    fontSize: 19,
    lineHeight: 24,
    color: colors.ink,
  },
  sectionCount: {
    ...type.caption,
    color: colors.muted,
    paddingBottom: 2,
  },
  carousel: {
    gap: 10,
    paddingRight: 18,
    paddingBottom: 5,
  },
  optionCard: {
    width: 302,
    borderRadius: radius.large,
    borderCurve: 'continuous',
    backgroundColor: colors.surface,
    padding: 15,
    borderWidth: 1,
    borderColor: colors.line,
    gap: 15,
    ...shadow,
  },
  optionTop: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
  },
  airlineBadge: {
    width: 34,
    height: 34,
    borderRadius: 11,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: colors.primarySoft,
  },
  optionHeading: {
    flex: 1,
  },
  optionRank: {
    ...type.caption,
    color: colors.primary,
  },
  optionAirline: {
    ...type.label,
    color: colors.ink,
  },
  optionPrice: {
    ...type.section,
    color: colors.ink,
  },
  legPriceWrap: {
    alignItems: 'flex-end',
  },
  legPriceLabel: {
    ...type.caption,
    color: colors.faint,
    fontSize: 9,
  },
  routeRow: {
    flexDirection: 'row',
    alignItems: 'center',
  },
  routeEnd: {
    width: 57,
  },
  airport: {
    ...type.title,
    fontSize: 21,
    lineHeight: 25,
    color: colors.ink,
  },
  mini: {
    ...type.caption,
    color: colors.muted,
    marginTop: 1,
  },
  routeLine: {
    flex: 1,
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 7,
  },
  routeDot: {
    width: 5,
    height: 5,
    borderRadius: 3,
    backgroundColor: colors.primary,
  },
  routeStroke: {
    flex: 1,
    height: 1,
    backgroundColor: colors.primarySoft,
  },
  routeArrival: {
    alignItems: 'flex-end',
  },
  flightFacts: {
    minHeight: 52,
    flexDirection: 'row',
    alignItems: 'center',
    borderRadius: radius.medium,
    paddingHorizontal: 9,
    backgroundColor: colors.surfaceTint,
  },
  flightFact: {
    flex: 1,
    alignItems: 'center',
  },
  flightFactValue: {
    ...type.caption,
    color: colors.ink,
  },
  flightFactLabel: {
    ...type.caption,
    color: colors.faint,
    fontSize: 9,
  },
  flightFactDivider: {
    width: 1,
    height: 25,
    backgroundColor: colors.line,
  },
  hotelCard: {
    width: 260,
    borderRadius: radius.large,
    borderCurve: 'continuous',
    overflow: 'hidden',
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.line,
    ...shadow,
  },
  hotelImageFrame: {
    height: 128,
    backgroundColor: colors.surfaceTint,
  },
  hotelImage: {
    width: '100%',
    height: '100%',
  },
  bestPill: {
    position: 'absolute',
    top: 9,
    left: 9,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
    paddingHorizontal: 8,
    paddingVertical: 5,
    borderRadius: radius.pill,
    backgroundColor: colors.surface,
  },
  bestPillText: {
    ...type.caption,
    color: colors.primary,
  },
  ratingPill: {
    position: 'absolute',
    right: 9,
    bottom: 9,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 3,
    paddingHorizontal: 7,
    paddingVertical: 4,
    borderRadius: radius.pill,
    backgroundColor: colors.surface,
  },
  ratingPillText: {
    ...type.caption,
    color: colors.ink,
  },
  hotelCopy: {
    padding: 13,
    gap: 3,
  },
  hotelName: {
    ...type.section,
    color: colors.ink,
  },
  hotelAddress: {
    ...type.caption,
    color: colors.muted,
  },
  hotelBottom: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'flex-end',
    marginTop: 8,
  },
  hotelPrice: {
    ...type.label,
    color: colors.ink,
  },
  hotelTotal: {
    ...type.caption,
    color: colors.faint,
    fontSize: 9,
  },
  distancePill: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 3,
    paddingHorizontal: 7,
    paddingVertical: 5,
    borderRadius: radius.pill,
    backgroundColor: colors.primarySoft,
  },
  distanceText: {
    ...type.caption,
    color: colors.primary,
  },
  selectionCardSelected: {
    borderColor: colors.primary,
    borderWidth: 2,
  },
  selectionAction: {
    minHeight: 40,
    borderRadius: radius.medium,
    borderCurve: 'continuous',
    backgroundColor: colors.primarySoft,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 7,
    marginTop: 2,
    paddingHorizontal: 12,
  },
  selectionActionSelected: {
    backgroundColor: colors.primary,
  },
  selectionActionText: {
    ...type.label,
    color: colors.primary,
  },
  selectionActionTextSelected: {
    color: colors.surface,
  },
  selectionHint: {
    ...type.caption,
    color: colors.muted,
    marginTop: 7,
  },
  selectionConfirmation: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: 9,
    borderRadius: radius.medium,
    borderCurve: 'continuous',
    backgroundColor: colors.greenSoft,
    paddingHorizontal: 12,
    paddingVertical: 11,
    marginVertical: 4,
  },
  selectionConfirmationIcon: {
    width: 24,
    height: 24,
    borderRadius: 8,
    borderCurve: 'continuous',
    backgroundColor: colors.green,
    alignItems: 'center',
    justifyContent: 'center',
  },
  selectionConfirmationText: {
    ...type.caption,
    color: colors.ink,
    flex: 1,
    paddingTop: 2,
  },
  planCard: {
    backgroundColor: colors.surfaceTint,
    borderColor: colors.surfaceViolet,
  },
  planSummary: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    paddingHorizontal: 2,
  },
  planTotal: {
    ...type.hero,
    fontSize: 31,
    lineHeight: 36,
    color: colors.ink,
  },
  planTotalLabel: {
    ...type.caption,
    color: colors.muted,
  },
  planShield: {
    width: 44,
    height: 44,
    borderRadius: 15,
    backgroundColor: colors.primarySoft,
    alignItems: 'center',
    justifyContent: 'center',
  },
  statGrid: {
    flexDirection: 'row',
    gap: 7,
  },
  statCard: {
    flex: 1,
    minWidth: 0,
    minHeight: 82,
    padding: 9,
    borderRadius: radius.medium,
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.line,
    alignItems: 'center',
    justifyContent: 'center',
  },
  statLabel: {
    ...type.caption,
    color: colors.muted,
    fontSize: 9,
  },
  statValue: {
    ...type.section,
    color: colors.ink,
    marginTop: 2,
    maxWidth: '100%',
  },
  statHelper: {
    ...type.caption,
    color: colors.faint,
    fontSize: 9,
    maxWidth: '100%',
  },
  inclusionSection: {
    gap: 9,
    borderRadius: radius.medium,
    padding: 11,
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.line,
  },
  inclusionTitle: {
    ...type.label,
    color: colors.ink,
  },
  inclusionGrid: {
    flexDirection: 'row',
  },
  inclusion: {
    flex: 1,
    minWidth: 0,
    alignItems: 'center',
    gap: 3,
    paddingHorizontal: 2,
  },
  inclusionIcon: {
    width: 33,
    height: 33,
    borderRadius: 11,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: colors.primarySoft,
  },
  inclusionLabel: {
    ...type.caption,
    color: colors.ink,
    fontSize: 9,
  },
  inclusionValue: {
    ...type.caption,
    color: colors.muted,
    fontSize: 8,
    maxWidth: '100%',
  },
  rejectedLine: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 7,
  },
  rejectedText: {
    ...type.caption,
    color: colors.green,
    flex: 1,
  },
  itineraryCard: {
    paddingHorizontal: 12,
    paddingTop: 15,
    paddingBottom: 12,
  },
  dayTabs: {
    gap: 7,
    paddingRight: 4,
  },
  dayTab: {
    minWidth: 68,
    minHeight: 49,
    borderRadius: radius.medium,
    borderWidth: 1,
    borderColor: colors.line,
    backgroundColor: colors.surface,
    alignItems: 'center',
    justifyContent: 'center',
    paddingHorizontal: 9,
  },
  dayTabActive: {
    backgroundColor: colors.primary,
    borderColor: colors.primary,
  },
  dayTabLabel: {
    ...type.caption,
    color: colors.ink,
  },
  dayTabLabelActive: {
    color: colors.surface,
  },
  dayTabDate: {
    ...type.caption,
    color: colors.faint,
    fontSize: 9,
  },
  dayTabDateActive: {
    color: colors.whiteMuted,
  },
  itineraryHero: {
    height: 172,
    overflow: 'hidden',
    borderRadius: radius.hero,
    backgroundColor: colors.navyDeep,
  },
  itineraryHeroCopy: {
    position: 'absolute',
    left: 16,
    right: 16,
    bottom: 14,
  },
  itineraryHeroEyebrow: {
    ...type.caption,
    color: colors.whiteMuted,
    textTransform: 'uppercase',
    letterSpacing: 0.65,
  },
  itineraryHeroTitle: {
    ...type.title,
    color: colors.surface,
    marginTop: 2,
  },
  timelineCard: {
    borderRadius: radius.medium,
    borderWidth: 1,
    borderColor: colors.line,
    backgroundColor: colors.surface,
    padding: 12,
    gap: 13,
  },
  timelineHeading: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  timelineEyebrow: {
    ...type.caption,
    color: colors.primary,
    textTransform: 'uppercase',
    letterSpacing: 0.6,
  },
  timelineTitle: {
    ...type.section,
    color: colors.ink,
  },
  timelineCount: {
    width: 30,
    height: 30,
    borderRadius: 10,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: colors.primarySoft,
  },
  timelineCountText: {
    ...type.label,
    color: colors.primary,
  },
  timeline: {
    gap: 0,
  },
  timelineRow: {
    minHeight: 70,
    flexDirection: 'row',
    alignItems: 'stretch',
  },
  timelineTimeColumn: {
    width: 57,
    paddingTop: 2,
  },
  itemTime: {
    ...type.caption,
    color: colors.muted,
  },
  timelineRail: {
    width: 31,
    alignItems: 'center',
  },
  timelineLine: {
    position: 'absolute',
    top: 18,
    bottom: -2,
    width: 2,
    backgroundColor: colors.surfaceViolet,
  },
  timelineDot: {
    width: 24,
    height: 24,
    borderRadius: 12,
    alignItems: 'center',
    justifyContent: 'center',
  },
  timelineCopy: {
    flex: 1,
    paddingBottom: 13,
    paddingTop: 1,
  },
  itemTitle: {
    ...type.label,
    color: colors.ink,
  },
  itemLocation: {
    ...type.caption,
    color: colors.primary,
    marginTop: 1,
  },
  itemDescription: {
    ...type.caption,
    color: colors.muted,
    marginTop: 2,
  },
  approvalCard: {
    backgroundColor: colors.surfaceTint,
    borderColor: colors.surfaceViolet,
  },
  approvalTop: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 11,
  },
  approvalIcon: {
    width: 47,
    height: 47,
    borderRadius: 15,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: colors.primarySoft,
  },
  approvalCopy: {
    flex: 1,
  },
  approvalEyebrow: {
    ...type.caption,
    color: colors.primary,
    textTransform: 'uppercase',
    letterSpacing: 0.65,
  },
  approvalTitle: {
    ...type.section,
    color: colors.ink,
  },
  approvalStats: {
    minHeight: 65,
    flexDirection: 'row',
    alignItems: 'center',
    borderRadius: radius.medium,
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.line,
  },
  approvalStat: {
    flex: 1,
    alignItems: 'center',
  },
  approvalStatValue: {
    ...type.section,
    color: colors.ink,
  },
  approvalStatLabel: {
    ...type.caption,
    color: colors.muted,
  },
  approvalStatDivider: {
    width: 1,
    height: 34,
    backgroundColor: colors.line,
  },
  safetyRow: {
    flexDirection: 'row',
    gap: 7,
    alignItems: 'flex-start',
  },
  safetyText: {
    ...type.caption,
    color: colors.green,
    flex: 1,
  },
  approvalActions: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
  },
  approve: {
    flex: 1.25,
    minHeight: 48,
    borderRadius: radius.medium,
    flexDirection: 'row',
    gap: 7,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: colors.primary,
  },
  approveText: {
    ...type.label,
    color: colors.surface,
  },
  secondary: {
    flex: 1,
    minHeight: 48,
    borderRadius: radius.medium,
    flexDirection: 'row',
    gap: 6,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.lineStrong,
  },
  secondaryText: {
    ...type.label,
    color: colors.primary,
  },
  cancel: {
    alignSelf: 'center',
    paddingHorizontal: 12,
    paddingVertical: 2,
  },
  cancelText: {
    ...type.caption,
    color: colors.muted,
  },
  calendarCard: {
    padding: 12,
  },
  calendarHero: {
    height: 124,
    overflow: 'hidden',
    borderRadius: radius.medium,
    backgroundColor: colors.navyDeep,
  },
  calendarTint: {
    position: 'absolute',
    top: 0,
    right: 0,
    bottom: 0,
    left: 0,
    backgroundColor: colors.navyDeep,
    opacity: 0.38,
  },
  calendarCheck: {
    position: 'absolute',
    top: 12,
    right: 12,
    width: 36,
    height: 36,
    borderRadius: 13,
    backgroundColor: colors.green,
    alignItems: 'center',
    justifyContent: 'center',
  },
  calendarDownload: {
    minHeight: 48,
    borderRadius: radius.medium,
    borderCurve: 'continuous',
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 7,
    backgroundColor: colors.primary,
  },
  successTitleRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 9,
    paddingHorizontal: 3,
  },
  successCopy: {
    flex: 1,
  },
  successEyebrow: {
    ...type.caption,
    color: colors.green,
    textTransform: 'uppercase',
    letterSpacing: 0.65,
  },
  successPill: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
    paddingHorizontal: 9,
    paddingVertical: 7,
    borderRadius: radius.pill,
    backgroundColor: colors.primarySoft,
  },
  successPillText: {
    ...type.caption,
    color: colors.primary,
  },
  metricGrid: {
    flexDirection: 'row',
    gap: 6,
  },
  metric: {
    flex: 1,
    minWidth: 0,
    minHeight: 91,
    borderRadius: radius.medium,
    backgroundColor: colors.surfaceTint,
    justifyContent: 'center',
    alignItems: 'center',
    paddingHorizontal: 3,
  },
  metricIcon: {
    width: 29,
    height: 29,
    borderRadius: 10,
    backgroundColor: colors.primarySoft,
    justifyContent: 'center',
    alignItems: 'center',
    marginBottom: 4,
  },
  metricValue: {
    ...type.label,
    color: colors.ink,
    maxWidth: '100%',
  },
  metricLabel: {
    ...type.caption,
    color: colors.muted,
    fontSize: 9,
  },
  reportNotice: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: 8,
    borderRadius: radius.medium,
    padding: 11,
    backgroundColor: colors.greenSoft,
  },
  reportNoticeText: {
    ...type.caption,
    color: colors.green,
    flex: 1,
  },
  systemCopy: {
    flex: 1,
    gap: 2,
  },
  errorBlock: {
    backgroundColor: colors.coralSoft,
    padding: 12,
    borderRadius: radius.medium,
  },
  errorMark: {
    backgroundColor: colors.coral,
  },
  errorLabel: {
    color: colors.coral,
  },
  pressed: {
    opacity: 0.72,
    transform: [{ scale: 0.985 }],
  },
});
