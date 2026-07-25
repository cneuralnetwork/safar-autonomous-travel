# Safar — autonomous travel planning, in one conversation

Safar is a mobile-first autonomous travel agent. A traveller describes a trip in
plain language, Safar resolves missing details in chat, searches flights and
hotels in parallel, applies hard budget and preference constraints, builds a
day-by-day itinerary, asks for approval, and then writes the approved plan to
Google Calendar.

The repository contains:

- `mobile/` — Expo SDK 57 React Native application
- `backend/` — FastAPI orchestration service
- `supabase/` — Postgres schema, RLS policies, and seed data
- `render.yaml` — reproducible Render deployment

## Product principles

- No travel forms: every clarification happens in the conversation.
- Google is the only sign-in method.
- Search and planning are safe; calendar writes always require approval.
- The LLM interprets intent, while deterministic code enforces constraints.
- Every task, retry, fallback, rejection, and external action is recorded.
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
missing. Google sign-in and Calendar require the corresponding Google and
Supabase configuration. See [API_SETUP.md](API_SETUP.md) for the exact dashboard
steps, redirect URLs, scopes, and environment variables.

## Architecture

```text
Expo mobile app
    │  HTTPS + polling
    ▼
FastAPI API ── OAuth bridge ── Google / Supabase Auth
    │
    ├── request interpreter (Sarvam-105B)
    ├── validated task graph
    ├── parallel travel tools
    ├── deterministic constraint solver
    ├── retry + fallback policy
    ├── itinerary composer
    └── approval gate ───────── Google Calendar
    │
    ▼
Supabase Postgres + RLS
```

## Verification

```bash
cd backend
uv run pytest
uv run ruff check .

cd ../mobile
npm run typecheck
npm test
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
