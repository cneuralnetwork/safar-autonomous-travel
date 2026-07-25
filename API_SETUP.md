# API and service configuration

Safar uses Google-only authentication. There is no password, magic-link, phone,
guest, Apple, or GitHub sign-in path.

## Required for production

### 1. Supabase

Create a project in the Mumbai region when available.

1. Apply every SQL file in `supabase/migrations/` in timestamp order. The
   production Safar project already has these migrations applied.
2. In **Authentication → Providers**, enable Google and disable all other
   providers, including email/password.
3. Copy the project URL, publishable key, and secret/service-role key.
4. Configure:

```env
SUPABASE_URL=https://uthdtiesqkjcblmyouuv.supabase.co
SUPABASE_PUBLISHABLE_KEY=sb_publishable_...
SUPABASE_SECRET_KEY=sb_secret_...
```

The publishable key is used by the mobile client. The secret key belongs only
on Render. The harness migration adds RLS-protected task/model/event tables and
publishes run updates to Supabase Realtime; do not expose the secret key to Expo.

### 2. Google Cloud

Configure an external OAuth consent screen. For testing, add judge Google
accounts as test users. Create a **Web application** OAuth client with:

```text
Authorized redirect URI:
https://uthdtiesqkjcblmyouuv.supabase.co/auth/v1/callback
https://safar-autonomous-travel.onrender.com/v1/auth/google/callback
```

Use the same Web client ID and secret in Supabase’s Google provider page.
The Supabase callback is registered because the provider must be enabled for
ID-token exchange; the Expo Go sign-in itself returns through the Render
callback bridge.

Set:

```env
GOOGLE_CLIENT_ID=794463475484-cd5fuuenapv78tb3infpoiruvebtbhtr.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=...
```

Basic sign-in requests only `openid`, `email`, and `profile`. Safar creates a
portable `.ics` file on the traveller’s device, so it does not request a Google
Calendar scope or access a calendar account.

Google is used only for account authentication. It is not used for maps,
geocoding, attraction search, hotel-distance verification, or calendar access.

### 3. OpenStreetMap

No map API key is required. Leaflet renders OpenStreetMap tiles in the Expo DOM
component on web, Android, and iOS. The backend resolves destinations with
Nominatim and queries named attractions through Overpass:

```env
NOMINATIM_BASE_URL=https://nominatim.openstreetmap.org
OVERPASS_BASE_URL=https://overpass-api.de/api
```

Safar rate-limits and caches Nominatim lookups, identifies itself with a custom
user agent, retains OpenStreetMap attribution, and stores the returned OSM
coordinates and entity links on itinerary places.

### 4. Sarvam AI

Create an API subscription key and set:

```env
SARVAM_API_KEY=...
SARVAM_MODEL=sarvam-105b
```

The backend uses the OpenAI-compatible
`https://api.sarvam.ai/v1/chat/completions` endpoint with strict JSON schemas
and the `api-subscription-key` header. In production, exhausted model retries
pause the run visibly instead of silently switching to a rule-only planner.

### 5. Travel search

Primary provider:

```env
SERPAPI_API_KEY=...
```

Safar calls SerpApi's `google_flights` and `google_hotels` engines.
Outbound and return options are two independent one-way flight searches. The
traveller chooses each leg before Safar searches stays and validates the final
combination.

Fallback provider:

```env
AMADEUS_CLIENT_ID=...
AMADEUS_CLIENT_SECRET=...
AMADEUS_ENV=production
```

Use Amadeus Self-Service production credentials for live prices. Test
credentials use `AMADEUS_ENV=test`.

Railway fallback:

```env
RAILRADAR_API_KEY=rr_live_...
RAILRADAR_BASE_URL=https://api.railradar.in
```

Create a RailRadar developer key at `https://railradar.in/docs`. Safar uses
RailRadar's station lookup and date-filtered trains-between-stations endpoints.
When a flight fallback starts, Sarvam may propose station-code candidates for
the two cities. Those codes are never trusted directly: Safar checks each one
against RailRadar's station lookup and rejects airport codes or stations whose
name does not match the requested city. A deterministic gateway map remains the
fallback when every model candidate is rejected.
RailRadar supplies railway schedules, train numbers, durations, intermediate
halts, and optional live operational data. It does not currently supply ticket
fares or seat availability, so Safar labels railway prices as estimates and
asks the traveller to verify inventory before booking.

When no direct flight works, Safar searches RailRadar and can join a train to
an OpenStreetMap/OSRM road connector for destinations without a practical
railhead. First/last-mile connector routing uses:

```env
OSRM_BASE_URL=https://router.project-osrm.org
```

Road distance and duration come from the mapped route. Safar does not present
standalone bus schedules until a live coach inventory provider is connected.

## Required application secrets

```env
PUBLIC_BASE_URL=https://safar-autonomous-travel.onrender.com
TOKEN_ENCRYPTION_KEY=<fernet-key>
```

Generate the encryption key locally:

```bash
cd backend
uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Render can generate this value automatically from `render.yaml`.

## Mobile environment

Create `mobile/.env`:

```env
EXPO_PUBLIC_API_URL=https://safar-autonomous-travel.onrender.com
EXPO_PUBLIC_SUPABASE_URL=https://uthdtiesqkjcblmyouuv.supabase.co
EXPO_PUBLIC_SUPABASE_PUBLISHABLE_KEY=sb_publishable_...
```

No secret should use an `EXPO_PUBLIC_` prefix.

## Optional operational services

- Sentry DSN for crash/error monitoring
- EAS project ID for development and store builds
- A custom API domain for a branded Google consent screen

The application does not require Redis, a separate maps SDK key in the mobile
bundle, a payment provider, or a flight-booking credential.
