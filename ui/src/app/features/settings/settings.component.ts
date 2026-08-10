import { CommonModule } from '@angular/common';
import { Component, OnInit, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';

import { ApiService } from '../../core/api.service';

/**
 * Settings editor.
 *
 * The settings payload is a free-form JSON object. Rather than build a
 * bespoke form for each leaf value (impractical and easy to drift from
 * the server-side defaults), the SPA renders the JSON in a textarea
 * and lets the user edit it directly. Validation happens server-side;
 * the response breaks the change down into ``applied``,
 * ``requires_restart``, and ``failed``.
 *
 * A future change can introduce per-section forms; this skeleton just
 * proves the round-trip.
 */
@Component({
  selector: 'app-settings',
  standalone: true,
  imports: [CommonModule, FormsModule],
  template: `
    <h1>Settings</h1>
    <p class="lmcp-muted">Edit the merged defaults + user overrides below. Restart-only keys trigger a banner.</p>
    <textarea [(ngModel)]="raw" rows="20" cols="80"></textarea>
    <div class="lmcp-row" style="margin: 8px 0;">
      <button (click)="apply()" [disabled]="busy()">apply</button>
      <button (click)="reload()">reload</button>
      <span class="lmcp-spacer"></span>
      @if (result()) {
        <span>
          applied: {{ result()!.applied.length }} ·
          requires restart: {{ result()!.requires_restart.length }}
        </span>
      }
    </div>
    @if (error()) {
      <p class="lmcp-error">{{ error() }}</p>
    }
  `,
})
export class SettingsComponent implements OnInit {
  private readonly api = inject(ApiService);
  raw = '';
  readonly result = signal<{ applied: string[]; requires_restart: string[]; failed: string[] } | null>(null);
  readonly error = signal<string | null>(null);
  readonly busy = signal<boolean>(false);

  async ngOnInit(): Promise<void> {
    await this.reload();
  }

  async reload(): Promise<void> {
    try {
      const settings = await this.api.settingsGet();
      this.raw = JSON.stringify(settings, null, 2);
      this.error.set(null);
    } catch (e) {
      this.error.set(`failed to load settings: ${(e as Error).message}`);
    }
  }

  async apply(): Promise<void> {
    let patch: Record<string, unknown>;
    try {
      patch = JSON.parse(this.raw);
    } catch (e) {
      this.error.set(`invalid JSON: ${(e as Error).message}`);
      return;
    }
    this.busy.set(true);
    try {
      const result = await this.api.settingsApply(patch);
      this.result.set(result);
      this.error.set(null);
      // Reload to pick up any defaults we missed.
      await this.reload();
    } catch (e) {
      this.error.set(`failed to apply: ${(e as Error).message}`);
    } finally {
      this.busy.set(false);
    }
  }
}