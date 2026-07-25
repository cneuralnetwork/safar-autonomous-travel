# Safar — autonomous travel planning, in one conversation

Safar is a mobile-first autonomous travel agent. A traveller describes a trip in
plain language, Safar resolves missing details in chat, searches outbound and
return journeys separately, and pauses for the traveller to choose each route
and stay. When a direct flight is unavailable, Safar can use RailRadar railway
schedules and OpenStreetMap road connections to build a multimodal alternative.
It then applies hard constraints and builds a day-by-day itinerary. The
finished plan can be downloaded as a portable calendar file.

The repository contains:

- `mobile/` — Expo SDK 57 React Native application
- `backend/` — FastAPI orchestration service
- `supabase/` — Postgres schema, RLS policies, and seed data
- `render.yaml` — reproducible Render deployment

## Product principles

- No travel forms: every clarification happens in the conversation.
- Google is the only sign-in method.
- Search and planning are safe; Safar never books or spends money.
- Outbound journey, return journey, and stay choices are persisted independently
  and can be changed by tapping a card or speaking naturally in chat.
- Railway schedules come from RailRadar. Since RailRadar does not expose fares
  or seat inventory, estimated rail prices are visibly labelled and never
  represented as confirmed tickets.
- The LLM interprets intent, while deterministic code enforces constraints.
- Every task, retry, fallback, rejection, and external action is recorded.
- Sarvam plans and replans through strict schemas; it cannot invent or execute
  tools outside Safar's registry.
- Sarvam can propose railway station codes, but RailRadar validates code
  existence and city relevance before any timetable query is made.
- Past trips and reusable preferences are persisted per Google account.
- Interrupted planned/running workflows resume from persisted state after a
  service restart; traveller choice checkpoints never auto-select an option.
- Real providers activate when configured. Deterministic demo providers keep
  local development and automated tests reliable.

## Quick start

### Backend

```bash
cd backend
cp .env.example .env
uv sync --dev
uv run uvicorn app.main:app --reload
```

The API starts at `http://127.0.0.1:8000`. Open `/docs` for the interactive API
reference and `/health` for a readiness response.

### Mobile

```bash
cd mobile
cp .env.example .env
npm install
npx expo start --tunnel
```

Scan the QR code with the current Expo Go client.

## Configuration

The application boots with demo travel providers when their live credentials are
missing. Google sign-in requires the corresponding Google and Supabase
configuration. Calendar export is local and requires no calendar-account access.
See [API_SETUP.md](API_SETUP.md) for the exact dashboard steps, redirect URLs,
scopes, and environment variables.

## Architecture

```text
Expo mobile app
    │  HTTPS + Supabase Realtime
    ▼
FastAPI API ── OAuth bridge ── Google / Supabase Auth
    │
    ├── Sarvam controller (interpret, plan, replan)
    ├── normalized, validated task graph
    ├── outbound choice → return choice → stay choice
    ├── deterministic constraint solver
    ├── retry + fallback policy
    ├── itinerary composer
    └── portable ICS export
    │
    ▼
Supabase Postgres + RLS + append-only events
```

The live UI subscribes to run, message, and agent-event changes. A cursor-based
`GET /v1/runs/{run_id}/events` catch-up endpoint and a low-frequency REST
snapshot reconcile missed subscriptions without turning the app into a polling
loop. Run leases prevent duplicate workers from executing the same persisted
graph after a service restart.

## Verification

```bash
cd backend
uv run pytest
uv run ruff check .

# Uses local SARVAM_API_KEY and prints sanitized telemetry only.
uv run python scripts/smoke_sarvam.py

cd ../mobile
npm run typecheck
npm test
npm run export:web

cd ../backend
docker build -t safar-api:local .
```

## Deployment

Push the public repository, then open the Render Blueprint link:

```text
https://dashboard.render.com/blueprint/new?repo=<PUBLIC_GITHUB_REPOSITORY_URL>
```

Render reads `render.yaml`, builds the FastAPI service, and prompts for secrets.
The mobile client can be shared through Expo Go during the hackathon and moved
to an EAS development or production build without backend changes.

## License

MIT
