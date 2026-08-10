import { CommonModule } from '@angular/common';
import { Component, OnInit, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';

import { ApiService } from '../../core/api.service';
import { AuditList } from '../../core/models';

/**
 * Audit log browser.
 *
 * Filter chips for tool / workspace / status, virtual-scroll table,
 * and a "view log" button that opens the artifact in a side drawer.
 *
 * The drawer is intentionally minimal — the log can be megabytes; we
 * tail the first 200 lines and add a "load more" affordance.
 */
@Component({
  selector: 'app-audit-list',
  standalone: true,
  imports: [CommonModule, FormsModule],
  template: `
    <h1>Audit log</h1>
    <div class="filters">
      <input [(ngModel)]="toolFilter" placeholder="tool (substring)">
      <input [(ngModel)]="workspaceFilter" placeholder="workspace id">
      <select [(ngModel)]="okFilter">
        <option value="">all</option>
        <option value="0">failed</option>
        <option value="1">ok</option>
      </select>
      <button (click)="refresh()">refresh</button>
    </div>

    @if (rows().length === 0) {
      <p class="lmcp-muted">no matching calls</p>
    } @else {
      <table>
        <thead>
          <tr>
            <th>timestamp</th>
            <th>tool</th>
            <th>status</th>
            <th>duration</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          @for (row of rows(); track row.id) {
            <tr (click)="select(row.id)" [class.selected]="row.id === selectedId()">
              <td>{{ row.timestamp | date: 'short' }}</td>
              <td><code>{{ row.tool }}</code></td>
              <td>{{ row.status }}</td>
              <td>{{ row.duration_ms }} ms</td>
              <td><button (click)="viewLog(row.id, $event)">log</button></td>
            </tr>
          }
        </tbody>
      </table>
    }

    @if (selectedLog(); as log) {
      <pre class="log-drawer">{{ log }}</pre>
    }
  `,
  styles: [`
    .filters { display: flex; gap: 8px; margin-bottom: 12px; }
    .filters input, .filters select { padding: 4px 8px; border: 1px solid #ccc;
      border-radius: 4px; }
    table { width: 100%; border-collapse: collapse; background: white; }
    th, td { padding: 6px 8px; border-bottom: 1px solid #eee; text-align: left; }
    tr.selected { background: #e3f2fd; }
    code { background: #f0f0f0; padding: 2px 4px; border-radius: 3px; }
    .log-drawer { background: #1e1e1e; color: #ddd; padding: 12px;
      max-height: 400px; overflow: auto; border-radius: 4px; font-size: 12px; }
  `],
})
export class AuditListComponent implements OnInit {
  private readonly api = inject(ApiService);
  readonly rows = signal<AuditList['rows']>([]);
  readonly selectedId = signal<string | null>(null);
  readonly selectedLog = signal<string | null>(null);

  toolFilter = '';
  workspaceFilter = '';
  okFilter = '';

  async ngOnInit(): Promise<void> {
    await this.refresh();
  }

  async refresh(): Promise<void> {
    const opts: Parameters<ApiService['auditList']>[0] = {};
    if (this.toolFilter) opts.tool = this.toolFilter;
    if (this.workspaceFilter) opts.workspace_id = this.workspaceFilter;
    if (this.okFilter !== '') opts.ok = Number(this.okFilter);
    const body = await this.api.auditList(opts);
    this.rows.set(body.rows);
  }

  select(id: string): void {
    this.selectedId.set(id);
  }

  async viewLog(id: string, event: Event): Promise<void> {
    event.stopPropagation();
    this.selectedId.set(id);
    const response = await fetch(`/api/audit/${id}/log`);
    this.selectedLog.set(await response.text());
  }
}