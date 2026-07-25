export type RunStatus =
  | 'interpreting'
  | 'awaiting_input'
  | 'planned'
  | 'running'
  | 'awaiting_approval'
  | 'completed'
  | 'failed';

export type TaskStatus =
  | 'waiting'
  | 'running'
  | 'completed'
  | 'retrying'
  | 'failed'
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
  | 'budget'
  | 'itinerary'
  | 'approval'
  | 'calendar'
  | 'report'
  | 'error';

export interface TravelConstraints {
  origin?: string;
  origin_airport?: string;
  destination?: string;
  destination_airport?: string;
  start_date?: string;
  end_date?: string;
  duration_days?: number;
  adults: number;
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
  remaining_budget: number;
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
  last_message?: string;
  created_at: string;
  updated_at: string;
}

export interface RunState {
  id: string;
  conversation_id: string;
  user_id: string;
  status: RunStatus;
  constraints: TravelConstraints;
  graph?: TaskGraph;
  flights: FlightOption[];
  hotels: HotelOption[];
  packages: PackageOption[];
  selected_package?: PackageOption;
  itinerary?: Itinerary;
  approval?: Approval;
  calendar_event_links: string[];
  provider_calls: number;
  retries: number;
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

