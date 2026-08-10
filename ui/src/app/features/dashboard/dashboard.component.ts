import { CommonModule } from '@angular/common';
import { Component, OnInit, inject, signal } from '@angular/core';

import { ApiService } from '../../core/api.service';
import { AuditList, BackgroundSummary, StatusPayload } from '../../core/models';

/**
 * Dashboard — top-level status snapshot.
 *
 * Composes four widgets:
 *
 * - Server status (pid, uptime, transport, audit DB ready)
 * - Background processes (managed dev servers + stop buttons)
 * - Recent calls (last 5 audit rows)
 * - Listening ports (derived from backgrounds, not a separate endpoint)
 */
@Component({
  selector: 'app-dashboard',
  standalone: true,
  imports: [CommonModule],
  template: `
    <h1>Dashboard</h1>
    @if (status(); as s) {
      <section class="card">
        <h2>Server</h2>
        <p>pid: <code>{{ s.server.pid }}</code></p>
        <p>uptime: {{ s.server.uptime_ms | number }} ms</p>
        <p>transport: <code>{{ s.server.transport }}</code></p>
        <p>data dir: <code>{{ s.data_dir }}</code></p>
      </section>
    } @else if (error()) {
      <p class="lmcp-error">{{ error() }}</p>
    } @else {
      <p class="lmcp-muted">loading…</p>
    }

    <section class="card">
      <h2>Background processes</h2>
      @if (backgrounds().length === 0) {
        <p class="lmcp-muted">no managed processes</p>
      } @else {
        <ul>
          @for (b of backgrounds(); track b.id) {
            <li>
              <code>{{ b.command }}</code>
              <span class="lmcp-muted">— pid {{ b.pid }} — {{ b.status }}</span>
              @if (b.status === 'running') {
                <button (click)="stopBackground(b.id)">stop</button>
              }
            </li>
          }
        </ul>
      }
    </section>

    <section class="card">
      <h2>Recent calls</h2>
      @if (recent().length === 0) {
        <p class="lmcp-muted">no calls yet</p>
      } @else {
        <table>
          <thead><tr><th>tool</th><th>status</th><th>duration</th></tr></thead>
          <tbody>
            @for (row of recent(); track row.id) {
              <tr>
                <td><code>{{ row.tool }}</code></td>
                <td>{{ row.status }}</td>
                <td>{{ row.duration_ms }} ms</td>
              </tr>
            }
          </tbody>
        </table>
      }
    </section>
  `,
  styles: [`
    .card { background: white; border: 1px solid #e0e0e0; border-radius: 8px;
      padding: 16px; margin-bottom: 16px; }
    h2 { margin-top: 0; }
    code { background: #f0f0f0; padding: 2px 4px; border-radius: 3px; }
    table { width: 100%; border-collapse: collapse; }
    th, td { text-align: left; padding: 4px 8px; border-bottom: 1px solid #eee; }
  `],
})
export class DashboardComponent implements OnInit {
  private readonly api = inject(ApiService);
  readonly status = signal<StatusPayload | null>(null);
  readonly backgrounds = signal<BackgroundSummary[]>([]);
  readonly recent = signal<AuditList['rows']>([]);
  readonly error = signal<string | null>(null);

  async ngOnInit(): Promise<void> {
    try {
      const [s, bg, audit] = await Promise.all([
        this.api.status(),
        this.api.backgroundsList(),
        this.api.auditList({ page_size: 5 }),
      ]);
      this.status.set(s);
      this.backgrounds.set(bg.processes);
      this.recent.set(audit.rows);
    } catch (e) {
      this.error.set(`failed to load dashboard: ${(e as Error).message}`);
    }
  }

  async stopBackground(id: string): Promise<void> {
    try {
      await this.api.backgroundStop(id);
      const fresh = await this.api.backgroundsList();
      this.backgrounds.set(fresh.processes);
    } catch (e) {
      this.error.set(`failed to stop process: ${(e as Error).message}`);
    }
  }
}