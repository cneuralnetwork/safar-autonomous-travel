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
on Render.

### 2. Google Cloud

Create one Google Cloud project and enable:

- Google Calendar API
- Places API (New), if live attraction search is required

Configure an external OAuth consent screen. For testing, add judge Google
accounts as test users. Create a **Web application** OAuth client with:

```text
Authorized redirect URI:
https://uthdtiesqkjcblmyouuv.supabase.co/auth/v1/callback
https://safar-api-ax08.onrender.com/v1/auth/google/callback
https://safar-api-ax08.onrender.com/v1/calendar/callback
```

Use the same Web client ID and secret in Supabase’s Google provider page.
The Supabase callback is registered because the provider must be enabled for
ID-token exchange; the Expo Go sign-in itself returns through the Render
callback bridge.

Set:

```env
GOOGLE_CLIENT_ID=794463475484-cd5fuuenapv78tb3infpoiruvebtbhtr.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=...
GOOGLE_MAPS_API_KEY=...
```

Basic sign-in requests only `openid`, `email`, and `profile`. Calendar consent
is requested later with:

```text
https://www.googleapis.com/auth/calendar.events.owned
```

Restrict `GOOGLE_MAPS_API_KEY` to Places API (New) and the Render service.
The same API verifies hotel distance when “near the beach” or another location
constraint is treated as a hard requirement.

### 3. Sarvam AI

Create an API subscription key and set:

```env
SARVAM_API_KEY=...
SARVAM_MODEL=sarvam-105b
```

The backend uses the OpenAI-compatible
`https://api.sarvam.ai/v1/chat/completions` endpoint with a strict JSON schema.

### 4. Travel search

Primary provider:

```env
SERPAPI_API_KEY=...
```

Safar calls SerpApi's `google_flights` and `google_hotels` engines.
Round-trip searches use SerpApi's documented `departure_token` follow-up so
the return legs are provider results rather than synthesized data.

Fallback provider:

```env
AMADEUS_CLIENT_ID=...
AMADEUS_CLIENT_SECRET=...
AMADEUS_ENV=production
```

Use Amadeus Self-Service production credentials for live prices. Test
credentials use `AMADEUS_ENV=test`.

## Required application secrets

```env
PUBLIC_BASE_URL=https://safar-api-ax08.onrender.com
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
EXPO_PUBLIC_API_URL=https://safar-api-ax08.onrender.com
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
