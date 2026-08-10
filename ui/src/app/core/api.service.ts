import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { firstValueFrom } from 'rxjs';

import {
  AuditList,
  BackgroundSummary,
  McpConfigSnippet,
  RulesList,
  RuleSummary,
  StatusPayload,
} from './models';

/**
 * Typed wrapper around ``HttpClient`` for the control plane.
 *
 * Every method targets the FastAPI app served at the same origin
 * (``/api/...``). Errors propagate as rejected promises; the
 * components decide how to surface them.
 */
@Injectable({ providedIn: 'root' })
export class ApiService {
  private readonly http = inject(HttpClient);

  status(): Promise<StatusPayload> {
    return firstValueFrom(this.http.get<StatusPayload>('/api/status'));
  }

  auditList(opts: {
    agent?: string;
    tool?: string;
    ok?: number;
    workspace_id?: string;
    page?: number;
    page_size?: number;
  } = {}): Promise<AuditList> {
    const params: Record<string, string | number> = {};
    for (const [k, v] of Object.entries(opts)) {
      if (v !== undefined && v !== null && v !== '') {
        params[k] = v;
      }
    }
    return firstValueFrom(this.http.get<AuditList>('/api/audit', { params }));
  }

  settingsGet(): Promise<Record<string, unknown>> {
    return firstValueFrom(this.http.get<Record<string, unknown>>('/api/settings'));
  }

  settingsApply(patch: Record<string, unknown>): Promise<{
    applied: string[];
    requires_restart: string[];
    failed: string[];
  }> {
    return firstValueFrom(
      this.http.post<{
        applied: string[];
        requires_restart: string[];
        failed: string[];
      }>('/api/settings', { patch }),
    );
  }

  rulesList(): Promise<RulesList> {
    return firstValueFrom(this.http.get<RulesList>('/api/rules'));
  }

  ruleToggle(id: string, enabled: boolean): Promise<{ id: string; enabled: boolean }> {
    return firstValueFrom(
      this.http.patch<{ id: string; enabled: boolean }>(`/api/rules/${id}`, { enabled }),
    );
  }

  rulesReload(): Promise<{ reloaded: number; errors: Array<Record<string, string>> }> {
    return firstValueFrom(
      this.http.post<{ reloaded: number; errors: Array<Record<string, string>> }>(
        '/api/rules/reload',
        {},
      ),
    );
  }

  backgroundsList(): Promise<{ processes: BackgroundSummary[] }> {
    return firstValueFrom(
      this.http.get<{ processes: BackgroundSummary[] }>('/api/backgrounds'),
    );
  }

  backgroundStop(id: string): Promise<BackgroundSummary> {
    return firstValueFrom(
      this.http.post<BackgroundSummary>(`/api/backgrounds/${id}/stop`, {}),
    );
  }

  mcpConfigSnippet(): Promise<McpConfigSnippet> {
    return firstValueFrom(this.http.get<McpConfigSnippet>('/api/mcp-config-snippet'));
  }

  shutdown(): Promise<{ ok: boolean; message: string }> {
    return firstValueFrom(
      this.http.post<{ ok: boolean; message: string }>('/api/shutdown', {}),
    );
  }
}