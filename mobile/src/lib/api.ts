import type {
  AgentEventPage,
  Approval,
  Conversation,
  ConversationSnapshot,
  RunState,
} from '@/types';

const baseUrl =
  process.env.EXPO_PUBLIC_API_URL?.replace(/\/$/, '') || 'http://127.0.0.1:8000';

export class ApiError extends Error {
  status: number;
  detail: unknown;

  constructor(status: number, detail: unknown) {
    super(typeof detail === 'string' ? detail : `Request failed with status ${status}`);
    this.status = status;
    this.detail = detail;
  }
}

async function apiRequest<T>(
  path: string,
  accessToken: string,
  init?: RequestInit,
): Promise<T> {
  const response = await fetch(`${baseUrl}${path}`, {
    ...init,
    headers: {
      Authorization: `Bearer ${accessToken}`,
      'Content-Type': 'application/json',
      ...init?.headers,
    },
  });
  if (!response.ok) {
    let detail: unknown = response.statusText;
    try {
      const payload = (await response.json()) as { detail?: unknown };
      detail = payload.detail ?? payload;
    } catch {
      // Preserve the HTTP status text.
    }
    throw new ApiError(response.status, detail);
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

export const api = {
  listConversations(accessToken: string) {
    return apiRequest<Conversation[]>('/v1/conversations', accessToken);
  },

  createConversation(
    accessToken: string,
    initialMessage?: string,
    resilienceDemo = false,
  ) {
    return apiRequest<ConversationSnapshot>('/v1/conversations', accessToken, {
      method: 'POST',
      body: JSON.stringify({
        initial_message: initialMessage,
        resilience_demo: resilienceDemo,
      }),
    });
  },

  getConversation(accessToken: string, conversationId: string) {
    return apiRequest<ConversationSnapshot>(
      `/v1/conversations/${conversationId}`,
      accessToken,
    );
  },

  listRunEvents(
    accessToken: string,
    runId: string,
    after = 0,
    limit = 200,
  ) {
    return apiRequest<AgentEventPage>(
      `/v1/runs/${runId}/events?after=${after}&limit=${limit}`,
      accessToken,
    );
  },

  sendMessage(
    accessToken: string,
    conversationId: string,
    text: string,
    resilienceDemo: boolean,
  ) {
    return apiRequest<RunState>(
      `/v1/conversations/${conversationId}/messages`,
      accessToken,
      {
        method: 'POST',
        body: JSON.stringify({
          text,
          idempotency_key: `${Date.now()}-${Math.random().toString(36).slice(2)}`,
          resilience_demo: resilienceDemo,
        }),
      },
    );
  },

  resolveApproval(
    accessToken: string,
    runId: string,
    approval: Approval,
    decision: 'approve' | 'edit' | 'cancel',
    editMessage?: string,
  ) {
    return apiRequest<RunState>(`/v1/runs/${runId}/approvals`, accessToken, {
      method: 'POST',
      body: JSON.stringify({
        decision,
        payload_hash: approval.payload_hash,
        edit_message: editMessage,
      }),
    });
  },

  calendarStatus(accessToken: string) {
    return apiRequest<{
      connected: boolean;
      authorization_status?: 'pending' | 'completed' | 'failed';
      error?: string;
    }>('/v1/calendar/status', accessToken);
  },

  connectCalendar(accessToken: string) {
    return apiRequest<{ authorization_url: string; expires_in: number }>(
      '/v1/calendar/connect',
      accessToken,
      { method: 'POST' },
    );
  },

  disconnectCalendar(accessToken: string) {
    return apiRequest<void>('/v1/calendar/connection', accessToken, {
      method: 'DELETE',
    });
  },
};

export const authApi = {
  async start(proofHash: string) {
    const response = await fetch(`${baseUrl}/v1/auth/google/start`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ proof_hash: proofHash }),
    });
    if (!response.ok) {
      const payload = (await response.json()) as { detail?: unknown };
      throw new ApiError(response.status, payload.detail ?? payload);
    }
    return (await response.json()) as {
      attempt_id: string;
      authorization_url: string;
      expires_in: number;
    };
  },

  async exchange(attemptId: string, proof: string) {
    const response = await fetch(`${baseUrl}/v1/auth/google/attempts/${attemptId}`, {
      headers: { 'X-Login-Proof': proof },
    });
    if (!response.ok) {
      const payload = (await response.json()) as { detail?: unknown };
      throw new ApiError(response.status, payload.detail ?? payload);
    }
    return (await response.json()) as {
      status: 'pending' | 'completed' | 'failed';
      session?: { access_token: string; refresh_token: string };
      error?: string;
    };
  },
};
