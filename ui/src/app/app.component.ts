import { Component, signal } from '@angular/core';
import { RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';

import { csrfReady, refreshCsrf } from './core/csrf.interceptor';

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
 * :class:`csrfInterceptor` once a control-plane session exists.
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
    @if (locked()) {
      <main class="lmcp-main lmcp-login">
        <h1>Sign in</h1>
        <p>
          Open the one-time login link printed when the server started, or
          paste the <code>admin_secret</code> from
          <code>server.json</code>. The MCP bearer token cannot sign in here.
        </p>
        <input #secret type="password" autocomplete="current-password"
          placeholder="admin secret" />
        <button type="button" (click)="signIn(secret)">Sign in</button>
        @if (error()) {
          <p class="lmcp-error">{{ error() }}</p>
        }
      </main>
    } @else {
      <nav class="lmcp-nav">
        <a routerLink="/dashboard" routerLinkActive="active">Dashboard</a>
        <a routerLink="/audit" routerLinkActive="active">Audit</a>
        <a routerLink="/approvals" routerLinkActive="active">Approvals</a>
        <a routerLink="/automation" routerLinkActive="active">Automation</a>
        <a routerLink="/settings" routerLinkActive="active">Settings</a>
        <a routerLink="/rules" routerLinkActive="active">Rules</a>
        <a routerLink="/mcp-config" routerLinkActive="active">MCP Config</a>
      </nav>
      <main class="lmcp-main">
        <router-outlet></router-outlet>
      </main>
    }
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
    .lmcp-login { max-width: 36rem; display: grid; gap: 12px; }
    .lmcp-login input { padding: 8px; font: inherit; }
    .lmcp-login button { justify-self: start; padding: 8px 16px; }
    .lmcp-error { color: #b00020; }
  `],
})
export class AppComponent {
  readonly locked = signal(true);
  readonly error = signal('');

  constructor() {
    void csrfReady().then((ok) => this.locked.set(!ok));
  }

  async signIn(input: HTMLInputElement): Promise<void> {
    this.error.set('');
    const token = input.value.trim();
    if (!token) {
      this.error.set('Paste the admin secret from server.json.');
      return;
    }
    const response = await fetch('/api/login', {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token }),
    });
    if (!response.ok) {
      this.error.set('Login rejected.');
      return;
    }
    await refreshCsrf();
    window.location.assign('/ui/');
  }
}