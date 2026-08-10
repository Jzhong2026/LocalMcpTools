import { CommonModule } from '@angular/common';
import { Component, OnInit, inject, signal } from '@angular/core';

import { ApiService } from '../../core/api.service';
import { RuleSummary } from '../../core/models';

/**
 * Safety rules list.
 *
 * Each row shows id, severity, suggestion, and a hit-count badge. A
 * toggle button enables/disables the rule via ``PATCH /api/rules/{id}``,
 * and a "Reload from disk" button hits ``POST /api/rules/reload``.
 */
@Component({
  selector: 'app-rules-list',
  standalone: true,
  imports: [CommonModule],
  template: `
    <h1>Safety rules</h1>
    <div class="lmcp-row" style="margin-bottom: 12px;">
      <button (click)="reload()">Reload from disk</button>
      @if (reloadResult()) {
        <span class="lmcp-muted">reloaded {{ reloadResult()!.reloaded }} rules</span>
      }
    </div>
    <table>
      <thead>
        <tr><th>id</th><th>severity</th><th>hits</th><th>suggestion</th><th></th></tr>
      </thead>
      <tbody>
        @for (rule of rules(); track rule.id) {
          <tr>
            <td><code>{{ rule.id }}</code></td>
            <td>{{ rule.severity }}</td>
            <td>{{ hitCount(rule.id) }}</td>
            <td>{{ rule.suggestion }}</td>
            <td><button (click)="toggle(rule.id)">toggle</button></td>
          </tr>
        }
      </tbody>
    </table>
  `,
  styles: [`
    table { width: 100%; border-collapse: collapse; background: white; }
    th, td { padding: 6px 8px; border-bottom: 1px solid #eee; text-align: left; }
    code { background: #f0f0f0; padding: 2px 4px; border-radius: 3px; }
  `],
})
export class RulesListComponent implements OnInit {
  private readonly api = inject(ApiService);
  readonly rules = signal<RuleSummary[]>([]);
  readonly hitStats = signal<Record<string, { hit_count: number }>>({});
  readonly reloadResult = signal<{ reloaded: number } | null>(null);
  readonly disabled = signal<Set<string>>(new Set());

  async ngOnInit(): Promise<void> {
    await this.reload();
  }

  async reload(): Promise<void> {
    const body = await this.api.rulesList();
    this.rules.set(body.rules);
    this.hitStats.set(body.hit_stats);
    const result = await this.api.rulesReload();
    this.reloadResult.set({ reloaded: result.reloaded });
  }

  hitCount(id: string): number {
    return this.hitStats()[id]?.hit_count ?? 0;
  }

  async toggle(id: string): Promise<void> {
    const disabled = this.disabled();
    const next = new Set(disabled);
    const willEnable = next.has(id);
    if (willEnable) next.delete(id);
    else next.add(id);
    this.disabled.set(next);
    await this.api.ruleToggle(id, willEnable);
  }
}