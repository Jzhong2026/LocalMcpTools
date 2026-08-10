import { CommonModule } from '@angular/common';
import { Component, OnInit, inject, signal } from '@angular/core';

import { ApiService } from '../../core/api.service';
import { McpConfigSnippet } from '../../core/models';

/**
 * MCP config snippet panel.
 *
 * Shows the ``mcp.json`` payload for the three supported transport
 * shapes — stdio (codebuddy), stdio (Copilot), and HTTP (shared mode).
 * Each snippet has a copy-to-clipboard button.
 */
@Component({
  selector: 'app-mcp-config',
  standalone: true,
  imports: [CommonModule],
  template: `
    <h1>MCP config snippets</h1>
    <p class="lmcp-muted">Drop one of these into the corresponding agent config file. See <code>docs/agent-configuration.md</code> for paths.</p>

    @if (snippet(); as s) {
      @for (key of keys(s); track key) {
        <section class="card">
          <h2>{{ key }}</h2>
          <p class="lmcp-muted">file: <code>{{ s[key].location }}</code></p>
          <pre>{{ format(s[key].content) }}</pre>
          <button (click)="copy(s[key].content)">copy</button>
        </section>
      }
    } @else {
      <p class="lmcp-muted">loading…</p>
    }
    @if (error()) {
      <p class="lmcp-error">{{ error() }}</p>
    }
  `,
  styles: [`
    .card { background: white; border: 1px solid #e0e0e0; border-radius: 8px;
      padding: 16px; margin-bottom: 16px; }
    pre { background: #f5f5f5; padding: 12px; border-radius: 4px;
      white-space: pre-wrap; word-break: break-all; }
    code { background: #f0f0f0; padding: 2px 4px; border-radius: 3px; }
  `],
})
export class McpConfigComponent implements OnInit {
  private readonly api = inject(ApiService);
  readonly snippet = signal<McpConfigSnippet | null>(null);
  readonly error = signal<string | null>(null);

  async ngOnInit(): Promise<void> {
    try {
      this.snippet.set(await this.api.mcpConfigSnippet());
    } catch (e) {
      this.error.set(`failed to load config: ${(e as Error).message}`);
    }
  }

  keys(s: McpConfigSnippet): Array<keyof McpConfigSnippet> {
    return Object.keys(s) as Array<keyof McpConfigSnippet>;
  }

  format(content: unknown): string {
    return JSON.stringify(content, null, 2);
  }

  async copy(content: unknown): Promise<void> {
    try {
      await navigator.clipboard.writeText(this.format(content));
    } catch (e) {
      this.error.set(`copy failed: ${(e as Error).message}`);
    }
  }
}