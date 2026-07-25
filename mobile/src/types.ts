export type RunStatus =
  | 'interpreting'
  | 'awaiting_input'
  | 'planning'
  | 'planned'
  | 'running'
  | 'replanning'
  | 'awaiting_approval'
  | 'paused'
  | 'completed'
  | 'failed';

export type AgentPhase =
  | 'interpreting'
  | 'planning'
  | 'executing'
  | 'replanning'
  | 'awaiting_input'
  | 'awaiting_approval'
  | 'finalizing'
  | 'completed'
  | 'paused'
  | 'failed';

export type TaskStatus =
  | 'waiting'
  | 'running'
  | 'completed'
  | 'retrying'
  | 'failed'
  | 'awaiting_input'
  | 'awaiting_approval'
  | 'skipped';

export type MessageKind =
  | 'text'
  | 'interpretation'
  | 'clarification'
  | 'task_graph'
  | 'operation'
  | 'flight_options'
  | 'hotel_options'
  | 'flight_selection'
  | 'hotel_selection'
  | 'selection_confirmation'
  | 'budget'
  | 'itinerary'
  | 'approval'
  | 'calendar'
  | 'report'
  | 'error';

export type VisualTheme =
  | 'coast'
  | 'mountains'
  | 'heritage'
  | 'nature'
  | 'city';

export interface TravelConstraints {
  origin?: string;
  origin_airport?: string;
  origin_station_codes: string[];
  destination?: string;
  destination_airport?: string;
  destination_station_codes: string[];
  visual_theme?: VisualTheme;
  start_date?: string;
  end_date?: string;
  duration_days?: number;
  adults?: number | null;
  children: number;
  budget?: number;
  currency: 'INR';
  earliest_departure?: string;
  hotel_area_preference?: string;
  max_hotel_distance_km?: number;
  preferences: string[];
  missing_fields: string[];
  inferred_fields: string[];
}

export interface TaskNode {
  id: string;
  title: string;
  description: string;
  tool_name: string;
  dependencies: string[];
  optional: boolean;
  status: TaskStatus;
  attempts: number;
  provider?: string;
  summary?: string;
  reason?: string;
}

export interface TaskGraph {
  goal: string;
  constraints: TravelConstraints;
  tasks: TaskNode[];
  estimated_steps: number;
}

export interface FlightSegment {
  airline: string;
  flight_number?: string;
  departure_airport: string;
  arrival_airport: string;
  departure_at: string;
  arrival_at: string;
  duration_minutes: number;
  mode: 'flight' | 'train' | 'bus' | 'transfer';
  service_name?: string;
  departure_name?: string;
  arrival_name?: string;
  data_quality: 'live' | 'scheduled' | 'estimated';
  data_source?: string;
  distance_km?: number;
  delay_minutes?: number;
  platform?: string;
}

export interface FlightOption {
  id: string;
  provider: string;
  outbound: FlightSegment[];
  inbound: FlightSegment[];
  total_price: number;
  currency: 'INR';
  stops: number;
  baggage?: string;
  route_type: 'direct' | 'connected' | 'multimodal';
  fare_is_estimate: boolean;
  schedule_is_live: boolean;
  source_note?: string;
  intermediate_stops: number;
}

export type SelectionKind =
  | 'outbound_flight'
  | 'return_flight'
  | 'hotel';

export interface FlightLegOption {
  id: string;
  provider: string;
  leg: 'outbound' | 'return';
  segments: FlightSegment[];
  total_price: number;
  currency: 'INR';
  stops: number;
  baggage?: string;
  booking_url?: string;
  route_type: 'direct' | 'connected' | 'multimodal';
  fare_is_estimate: boolean;
  schedule_is_live: boolean;
  source_note?: string;
  intermediate_stops: number;
}

export interface HotelOption {
  id: string;
  provider: string;
  name: string;
  address: string;
  rating: number;
  review_count: number;
  nightly_price: number;
  total_price: number;
  distance_to_preference_km?: number;
  latitude?: number;
  longitude?: number;
  image_url?: string;
  available: boolean;
}

export interface PackageOption {
  id: string;
  flight: FlightOption;
  hotel: HotelOption;
  on_trip_reserve: number;
  local_transfer_reserve: number;
  total_price: number;
  remaining_budget?: number | null;
  score: number;
}

export interface ItineraryItem {
  id: string;
  title: string;
  description: string;
  start_at: string;
  end_at: string;
  location?: string;
  latitude?: number;
  longitude?: number;
  category: 'flight' | 'hotel' | 'activity' | 'transfer' | 'meal' | 'buffer';
}

export interface ItineraryDay {
  date: string;
  title: string;
  items: ItineraryItem[];
}

export interface Itinerary {
  timezone: string;
  days: ItineraryDay[];
}

export interface Approval {
  action: 'add_calendar_events';
  event_count: number;
  estimated_trip_total: number;
  currency: 'INR';
  disclaimer: string;
  payload_hash: string;
  expires_at: string;
}

export interface ChatMessage {
  id: string;
  conversation_id: string;
  run_id?: string;
  role: 'user' | 'assistant' | 'system' | 'tool';
  kind: MessageKind;
  text: string;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface Conversation {
  id: string;
  user_id: string;
  title: string;
  destination?: string;
  visual_theme?: VisualTheme;
  last_message?: string;
  created_at: string;
  updated_at: string;
}

export interface RunState {
  id: string;
  conversation_id: string;
  user_id: string;
  status: RunStatus;
  phase: AgentPhase;
  harness_version: number;
  constraints: TravelConstraints;
  graph?: TaskGraph;
  outbound_flights: FlightLegOption[];
  return_flights: FlightLegOption[];
  selected_outbound_id?: string;
  selected_return_id?: string;
  selected_hotel_id?: string;
  selection_stage?: SelectionKind;
  flights: FlightOption[];
  hotels: HotelOption[];
  packages: PackageOption[];
  selected_package?: PackageOption;
  itinerary?: Itinerary;
  approval?: Approval;
  calendar_event_links: string[];
  provider_calls: number;
  retries: number;
  agent_cycles: number;
  model_calls: number;
  replans: number;
  assumptions: string[];
  station_resolution_attempted: boolean;
  last_event_id?: number;
  resilience_demo: boolean;
}

export interface ConversationSnapshot {
  conversation: Conversation;
  messages: ChatMessage[];
  active_run?: RunState;
}

export interface OperationEvent {
  task_id: string;
  status: TaskStatus;
  summary: string;
  reason?: string;
  timestamp: string;
}

export interface AgentEvent {
  id: number;
  run_id: string;
  conversation_id: string;
  user_id: string;
  type: string;
  phase: AgentPhase;
  status: string;
  summary: string;
  reason?: string;
  task_id?: string;
  provider?: string;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface AgentEventPage {
  items: AgentEvent[];
  next_after: number;
}
