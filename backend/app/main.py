from __future__ import annotations

from contextlib import asynccontextmanager
from html import escape
from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

from app.agent_model import SarvamGateway
from app.auth import (
    AuthConfigurationError,
    GoogleAuthBridge,
    GoogleAuthStartRequest,
    GoogleAuthStartResponse,
    get_current_user,
)
from app.config import Settings, get_settings
from app.interpreter import RequestInterpreter
from app.models import (
    AgentEventPage,
    ApprovalDecision,
    Conversation,
    ConversationSnapshot,
    CreateConversationRequest,
    ExecutionReport,
    RunState,
    SendMessageRequest,
    TripSelectionRequest,
    UserIdentity,
)
from app.orchestrator import Orchestrator
from app.store import create_store
from app.travel_tools import TravelToolRegistry

AuthenticatedUser = Annotated[UserIdentity, Depends(get_current_user)]


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    if settings.production and settings.auth_disabled:
        raise RuntimeError("AUTH_DISABLED cannot be enabled in production")
    if settings.production and not settings.sarvam_api_key:
        raise RuntimeError("SARVAM_API_KEY is required in production")
    store = create_store(settings)
    await store.initialize()
    sarvam_gateway = SarvamGateway(settings)
    app.state.settings = settings
    app.state.store = store
    app.state.auth_bridge = GoogleAuthBridge(settings)
    app.state.sarvam_gateway = sarvam_gateway
    app.state.orchestrator = Orchestrator(
        store,
        RequestInterpreter(settings, sarvam_gateway),
        TravelToolRegistry(settings),
    )
    await app.state.orchestrator.recover_pending_runs()
    try:
        yield
    finally:
        await app.state.orchestrator.close()
        await app.state.auth_bridge.close()
        await sarvam_gateway.close()
        await store.close()


app = FastAPI(
    title="Safar API",
    version="0.1.0",
    description=(
        "Conversation-first autonomous travel planning with deterministic constraints "
        "and portable calendar exports."
    ),
    lifespan=lifespan,
)
settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=settings.cors_origins != ["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    max_age=86_400,
)


def orchestrator(request: Request) -> Orchestrator:
    return request.app.state.orchestrator


@app.get("/", tags=["system"])
async def root() -> dict[str, str]:
    return {
        "name": "Safar API",
        "status": "ready",
        "docs": "/docs",
        "health": "/health",
    }


@app.get("/health", tags=["system"])
async def health(request: Request) -> dict[str, object]:
    app_settings: Settings = request.app.state.settings
    tools: TravelToolRegistry = request.app.state.orchestrator.tools
    return {
        "status": "ok",
        "environment": app_settings.app_env,
        "storage": "supabase" if app_settings.has_supabase else "sqlite",
        "travel_providers": tools.provider_names(),
        "sarvam": bool(app_settings.sarvam_api_key),
        "sarvam_model": app_settings.sarvam_model,
        "agent_harness": "v3",
        "google_auth": app_settings.google_auth_ready,
        "ics_export": True,
    }


@app.post(
    "/v1/auth/google/start",
    response_model=GoogleAuthStartResponse,
    tags=["authentication"],
)
async def start_google_auth(
    payload: GoogleAuthStartRequest, request: Request
) -> GoogleAuthStartResponse:
    try:
        return request.app.state.auth_bridge.start(payload.proof_hash)
    except AuthConfigurationError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


@app.get(
    "/v1/auth/google/callback",
    response_class=HTMLResponse,
    tags=["authentication"],
)
async def google_auth_callback(
    request: Request,
    state: str | None = Query(default=None),
    code: str | None = Query(default=None),
    error: str | None = Query(default=None),
) -> HTMLResponse:
    attempt = await request.app.state.auth_bridge.callback(state, code, error)
    success = attempt.status == "completed"
    title = "Signed in" if success else "Sign-in wasn’t completed"
    message = (
        "You can close this page and return to Safar."
        if success
        else escape(attempt.error or "Please return to Safar and try again.")
    )
    return _oauth_result_page(title, message, success)


@app.get(
    "/v1/auth/google/attempts/{attempt_id}",
    tags=["authentication"],
)
async def exchange_google_auth(
    attempt_id: str,
    request: Request,
    x_login_proof: Annotated[str, Header(min_length=32)],
):
    return await request.app.state.auth_bridge.exchange(attempt_id, x_login_proof)


@app.post(
    "/v1/conversations",
    response_model=ConversationSnapshot,
    status_code=status.HTTP_201_CREATED,
    tags=["conversations"],
)
async def create_conversation(
    payload: CreateConversationRequest,
    request: Request,
    user: AuthenticatedUser,
) -> ConversationSnapshot:
    return await orchestrator(request).create_conversation(
        user, payload.initial_message, payload.resilience_demo
    )


@app.get(
    "/v1/conversations",
    response_model=list[Conversation],
    tags=["conversations"],
)
async def list_conversations(request: Request, user: AuthenticatedUser) -> list[Conversation]:
    return await request.app.state.store.list_conversations(user.id)


@app.get(
    "/v1/conversations/{conversation_id}",
    response_model=ConversationSnapshot,
    tags=["conversations"],
)
async def get_conversation(
    conversation_id: UUID,
    request: Request,
    user: AuthenticatedUser,
) -> ConversationSnapshot:
    try:
        return await orchestrator(request).snapshot(user, conversation_id)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@app.post(
    "/v1/conversations/{conversation_id}/messages",
    response_model=RunState,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["conversations"],
)
async def send_message(
    conversation_id: UUID,
    payload: SendMessageRequest,
    request: Request,
    user: AuthenticatedUser,
) -> RunState:
    try:
        return await orchestrator(request).handle_message(user, conversation_id, payload)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@app.post(
    "/v1/runs/{run_id}/selections",
    response_model=RunState,
    tags=["runs"],
)
async def select_trip_option(
    run_id: UUID,
    payload: TripSelectionRequest,
    request: Request,
    user: AuthenticatedUser,
) -> RunState:
    try:
        return await orchestrator(request).select_option(user, run_id, payload)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.post(
    "/v1/runs/{run_id}/approvals",
    response_model=RunState,
    tags=["approvals"],
)
async def resolve_approval(
    run_id: UUID,
    payload: ApprovalDecision,
    request: Request,
    user: AuthenticatedUser,
) -> RunState:
    try:
        return await orchestrator(request).approve(user, run_id, payload)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.get(
    "/v1/runs/{run_id}/report",
    response_model=ExecutionReport,
    tags=["reports"],
)
async def get_report(
    run_id: UUID,
    request: Request,
    user: AuthenticatedUser,
) -> ExecutionReport:
    try:
        return await orchestrator(request).report(user, run_id)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@app.get(
    "/v1/runs/{run_id}/events",
    response_model=AgentEventPage,
    tags=["runs"],
)
async def list_run_events(
    run_id: UUID,
    request: Request,
    user: AuthenticatedUser,
    after: int = Query(default=0, ge=0),
    limit: int = Query(default=200, ge=1, le=500),
) -> AgentEventPage:
    run = await request.app.state.store.get_run(run_id, user.id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    items = await request.app.state.store.list_events(
        run_id,
        user.id,
        after_id=after,
        limit=limit,
    )
    return AgentEventPage(
        items=items,
        next_after=items[-1].id if items and items[-1].id is not None else after,
    )


def _oauth_result_page(title: str, message: str, success: bool) -> HTMLResponse:
    accent = "#49A676" if success else "#EF4456"
    accent_soft = "#E8F5EF" if success else "#FFF0F2"
    icon = "✓" if success else "!"
    return HTMLResponse(
        f"""
        <!doctype html>
        <html lang="en">
          <head>
            <meta charset="utf-8" />
            <meta name="viewport" content="width=device-width, initial-scale=1" />
            <title>{escape(title)} · Safar</title>
            <style>
              * {{ box-sizing: border-box; }}
              body {{
                margin: 0;
                min-height: 100svh;
                display: grid;
                place-items: center;
                padding: 24px;
                overflow: hidden;
                color: #11184c;
                background:
                  radial-gradient(circle at 18% 12%, #ffffff 0 7%, transparent 32%),
                  linear-gradient(160deg, #fafafd 0%, #efebfc 55%, #dcd7f7 100%);
                font-family: Poppins, ui-rounded, "Avenir Next", sans-serif;
              }}
              body::before, body::after {{
                content: "";
                position: fixed;
                z-index: -1;
                width: 360px;
                height: 360px;
                border-radius: 50%;
                filter: blur(2px);
              }}
              body::before {{
                left: -210px;
                bottom: -190px;
                background: #4c3dd4;
                opacity: .18;
              }}
              body::after {{
                right: -230px;
                top: -210px;
                background: #11184c;
                opacity: .08;
              }}
              main {{
                width: min(390px, 100%);
                overflow: hidden;
                border: 1px solid #e9e8f0;
                border-radius: 24px;
                background: #ffffff;
                box-shadow: 0 18px 48px rgba(17, 24, 76, .13);
              }}
              .brand {{
                display: flex;
                align-items: center;
                gap: 10px;
                padding: 18px 20px;
                border-bottom: 1px solid #e9e8f0;
                font-size: 16px;
                font-weight: 700;
              }}
              .brand-mark {{
                display: grid;
                place-items: center;
                width: 36px;
                height: 36px;
                border-radius: 12px;
                color: #ffffff;
                background: linear-gradient(135deg, #4c3dd4, #574ad6);
                transform: rotate(-8deg);
              }}
              .content {{ padding: 28px 24px 30px; }}
              .result-icon {{
                display: grid;
                place-items: center;
                width: 54px;
                height: 54px;
                border: 1px solid {accent};
                border-radius: 18px;
                color: {accent};
                background: {accent_soft};
                font-size: 25px;
                font-weight: 700;
              }}
              h1 {{
                margin: 20px 0 8px;
                font-size: 25px;
                line-height: 1.2;
                letter-spacing: -.5px;
              }}
              p {{
                margin: 0;
                color: #686b8d;
                font-size: 14px;
                line-height: 1.65;
              }}
              .hint {{
                display: flex;
                align-items: center;
                gap: 8px;
                margin-top: 22px;
                padding: 12px 13px;
                border-radius: 13px;
                color: #1d2258;
                background: #f5f2fc;
                font-size: 12px;
                line-height: 1.45;
              }}
              .hint-dot {{
                width: 8px;
                height: 8px;
                flex: 0 0 auto;
                border-radius: 50%;
                background: #4c3dd4;
              }}
              @media (prefers-reduced-motion: no-preference) {{
                main {{ animation: arrive .42s cubic-bezier(.2,.8,.2,1) both; }}
                @keyframes arrive {{
                  from {{ opacity: 0; transform: translateY(12px) scale(.985); }}
                  to {{ opacity: 1; transform: translateY(0) scale(1); }}
                }}
              }}
            </style>
          </head>
          <body>
            <main>
              <div class="brand">
                <span class="brand-mark" aria-hidden="true">✈</span>
                <span>Safar</span>
              </div>
              <div class="content">
                <div class="result-icon" aria-hidden="true">{icon}</div>
                <h1>{escape(title)}</h1>
                <p>{message}</p>
                <div class="hint">
                  <span class="hint-dot" aria-hidden="true"></span>
                  <span>You can safely close this window and continue in Safar.</span>
                </div>
              </div>
            </main>
          </body>
        </html>
        """
    )
