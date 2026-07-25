from __future__ import annotations

from contextlib import asynccontextmanager
from html import escape
from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

from app.auth import (
    AuthConfigurationError,
    GoogleAuthBridge,
    GoogleAuthStartRequest,
    GoogleAuthStartResponse,
    get_current_user,
)
from app.calendar_service import CalendarService
from app.config import Settings, get_settings
from app.interpreter import RequestInterpreter
from app.models import (
    ApprovalDecision,
    Conversation,
    ConversationSnapshot,
    CreateConversationRequest,
    ExecutionReport,
    RunState,
    SendMessageRequest,
    UserIdentity,
)
from app.orchestrator import CalendarConnectionRequired, Orchestrator
from app.store import TokenCipher, create_store
from app.travel_tools import TravelToolRegistry

AuthenticatedUser = Annotated[UserIdentity, Depends(get_current_user)]


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    if settings.production and settings.auth_disabled:
        raise RuntimeError("AUTH_DISABLED cannot be enabled in production")
    store = create_store(settings)
    await store.initialize()
    cipher = TokenCipher(settings.token_encryption_key)
    calendar = CalendarService(settings, store, cipher)
    app.state.settings = settings
    app.state.store = store
    app.state.auth_bridge = GoogleAuthBridge(settings)
    app.state.calendar = calendar
    app.state.orchestrator = Orchestrator(
        store,
        RequestInterpreter(settings),
        TravelToolRegistry(settings),
        calendar,
    )
    await app.state.orchestrator.recover_pending_runs()
    yield


app = FastAPI(
    title="Safar API",
    version="0.1.0",
    description=(
        "Conversation-first autonomous travel planning with deterministic constraints "
        "and approval-gated Google Calendar writes."
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
)


def orchestrator(request: Request) -> Orchestrator:
    return request.app.state.orchestrator


def calendar_service(request: Request) -> CalendarService:
    return request.app.state.calendar


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
        "google_auth": app_settings.google_auth_ready,
        "calendar": bool(app_settings.google_client_id and app_settings.google_client_secret),
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
    except CalendarConnectionRequired as error:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "calendar_connection_required",
                "message": str(error),
            },
        ) from error
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


@app.post("/v1/calendar/connect", tags=["calendar"])
async def connect_calendar(
    request: Request,
    user: AuthenticatedUser,
) -> dict[str, object]:
    try:
        return calendar_service(request).start(user)
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


@app.get("/v1/calendar/callback", response_class=HTMLResponse, tags=["calendar"])
async def calendar_callback(
    request: Request,
    state: str | None = Query(default=None),
    code: str | None = Query(default=None),
    error: str | None = Query(default=None),
) -> HTMLResponse:
    try:
        attempt = await calendar_service(request).callback(state, code, error)
        success = attempt.status == "completed"
        title = "Calendar connected" if success else "Calendar wasn’t connected"
        message = (
            "Return to Safar to approve the calendar events."
            if success
            else escape(attempt.error or "Return to Safar and try again.")
        )
        return _oauth_result_page(title, message, success)
    except Exception as callback_error:
        return _oauth_result_page("Calendar wasn’t connected", escape(str(callback_error)), False)


@app.get("/v1/calendar/status", tags=["calendar"])
async def calendar_status(
    request: Request,
    user: AuthenticatedUser,
) -> dict[str, object]:
    return await calendar_service(request).status(user.id)


@app.delete(
    "/v1/calendar/connection",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    tags=["calendar"],
)
async def disconnect_calendar(
    request: Request,
    user: AuthenticatedUser,
) -> Response:
    await calendar_service(request).disconnect(user.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _oauth_result_page(title: str, message: str, success: bool) -> HTMLResponse:
    accent = "#139FDB" if success else "#E96A5D"
    icon = "✓" if success else "!"
    return HTMLResponse(
        f"""
        <!doctype html>
        <html lang="en">
          <head>
            <meta charset="utf-8" />
            <meta name="viewport" content="width=device-width, initial-scale=1" />
            <title>{escape(title)} · Safar</title>
          </head>
          <body style="margin:0;background:#efefed;font-family:-apple-system,BlinkMacSystemFont,
                       'Segoe UI',sans-serif;color:#17181a;display:grid;place-items:center;
                       min-height:100vh">
            <main style="width:min(360px,calc(100% - 40px));background:#fff;border-radius:28px;
                         padding:32px;box-sizing:border-box;box-shadow:0 12px 40px #00000012">
              <div style="width:52px;height:52px;border-radius:18px;background:{accent};
                          color:white;display:grid;place-items:center;font-size:28px;
                          font-weight:800">{icon}</div>
              <h1 style="font-size:28px;line-height:1.1;margin:24px 0 10px">{escape(title)}</h1>
              <p style="font-size:16px;line-height:1.5;color:#6f7377;margin:0">{message}</p>
            </main>
          </body>
        </html>
        """
    )
