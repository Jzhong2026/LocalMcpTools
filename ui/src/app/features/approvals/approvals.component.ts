import { CommonModule } from '@angular/common';
import { Component, OnInit, inject, signal } from '@angular/core';

import { ApiService } from '../../core/api.service';
import { ApprovalSummary, WorkspaceSummary } from '../../core/models';

/**
 * Operator page for workspace profiles and one-shot action approvals.
 *
 * These controls talk only to the HTTP control plane. They are not MCP
 * tools, so an agent cannot grant itself a profile or an approval.
 */
@Component({
  selector: 'app-approvals',
  standalone: true,
  imports: [CommonModule],
  template: `
    <h1>Approvals</h1>
    <p class="lmcp-muted">
      Execution tools stay on <code>observe</code> until you raise the workspace
      profile here (or with <code>localmcptools set-profile</code>). Each command
      still needs its own approval.
    </p>

    <h2>Workspaces</h2>
    <table>
      <thead>
        <tr><th>id</th><th>root</th><th>profile</th></tr>
      </thead>
      <tbody>
        @for (ws of workspaces(); track ws.workspace_id) {
          <tr>
            <td><code>{{ ws.workspace_id }}</code></td>
            <td>{{ ws.canonical_root }}</td>
            <td>
              <select
                [value]="ws.profile"
                (change)="setProfile(ws.workspace_id, $any($event.target).value)"
              >
                @for (profile of profiles; track profile) {
                  <option [value]="profile">{{ profile }}</option>
                }
              </select>
            </td>
          </tr>
        }
      </tbody>
    </table>

    <h2>Pending actions</h2>
    <div class="lmcp-row" style="margin-bottom: 12px;">
      <button (click)="refresh()">Refresh</button>
      @if (message()) { <span class="lmcp-muted">{{ message() }}</span> }
    </div>
    <table>
      <thead>
        <tr><th>id</th><th>capability</th><th>preview</th><th></th></tr>
      </thead>
      <tbody>
        @for (item of approvals(); track item.id) {
          <tr>
            <td><code>{{ item.id }}</code></td>
            <td>{{ item.requested_capability }}</td>
            <td><pre>{{ previewText(item) }}</pre></td>
            <td><button (click)="approve(item.id)">Approve</button></td>
          </tr>
        } @empty {
          <tr><td colspan="4" class="lmcp-muted">No pending approvals.</td></tr>
        }
      </tbody>
    </table>
  `,
  styles: [`
    table { width: 100%; border-collapse: collapse; background: white; margin-bottom: 24px; }
    th, td { padding: 6px 8px; border-bottom: 1px solid #eee; text-align: left; vertical-align: top; }
    code, pre { background: #f0f0f0; padding: 2px 4px; border-radius: 3px; }
    pre { white-space: pre-wrap; margin: 0; }
  `],
})
export class ApprovalsComponent implements OnInit {
  private readonly api = inject(ApiService);
  readonly profiles = ['observe', 'workspace_exec', 'managed_process', 'interactive_ui'];
  readonly workspaces = signal<WorkspaceSummary[]>([]);
  readonly approvals = signal<ApprovalSummary[]>([]);
  readonly message = signal<string>('');

  async ngOnInit(): Promise<void> {
    await this.refresh();
  }

  async refresh(): Promise<void> {
    const [workspaces, approvals] = await Promise.all([
      this.api.workspacesList(),
      this.api.approvalsList('pending'),
    ]);
    this.workspaces.set(workspaces.workspaces);
    this.approvals.set(approvals.approvals);
  }

  previewText(item: ApprovalSummary): string {
    return JSON.stringify(item.preview ?? {}, null, 2);
  }

  async setProfile(workspaceId: string, profile: string): Promise<void> {
    await this.api.setWorkspaceProfile(workspaceId, profile);
    this.message.set(`profile set to ${profile}`);
    await this.refresh();
  }

  async approve(approvalId: string): Promise<void> {
    await this.api.approve(approvalId);
    this.message.set(`approved ${approvalId}`);
    await this.refresh();
  }
}
