import { Component } from '@angular/core';
import { RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';

/**
 * Application shell.
 *
 * The SPA is intentionally minimal:
 *
 * - Top bar with the server name.
 * - Side nav with five pages: Dashboard, Audit, Settings, Rules,
 *   MCP-config.
 * - ``<router-outlet>`` for the routed feature component.
 *
 * Each feature component talks to the control plane via
 * :class:`ApiService`. The CSRF header is added automatically by the
 * :class:`csrfInterceptor` once the bootstrap token is fetched.
 */
@Component({
  selector: 'app-root',
  standalone: true,
  imports: [RouterOutlet, RouterLink, RouterLinkActive],
  template: `
    <header class="lmcp-header">
      <span class="lmcp-brand">LocalMcpTools</span>
      <span class="lmcp-spacer"></span>
      <span class="lmcp-muted">control plane</span>
    </header>
    <nav class="lmcp-nav">
      <a routerLink="/dashboard" routerLinkActive="active">Dashboard</a>
      <a routerLink="/audit" routerLinkActive="active">Audit</a>
      <a routerLink="/automation" routerLinkActive="active">Automation</a>
      <a routerLink="/settings" routerLinkActive="active">Settings</a>
      <a routerLink="/rules" routerLinkActive="active">Rules</a>
      <a routerLink="/mcp-config" routerLinkActive="active">MCP Config</a>
    </nav>
    <main class="lmcp-main">
      <router-outlet></router-outlet>
    </main>
  `,
  styles: [`
    :host { display: grid; grid-template-rows: auto auto 1fr; min-height: 100vh; }
    .lmcp-header { display: flex; align-items: center; padding: 12px 24px;
      background: #1976d2; color: white; font-weight: 500; }
    .lmcp-brand { font-size: 1.1rem; }
    .lmcp-nav { display: flex; gap: 16px; padding: 8px 24px;
      background: #f5f5f5; border-bottom: 1px solid #e0e0e0; }
    .lmcp-nav a { padding: 6px 12px; border-radius: 4px; color: #333; }
    .lmcp-nav a.active { background: #1976d2; color: white; }
    .lmcp-main { padding: 24px; }
  `],
})
export class AppComponent {}