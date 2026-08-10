import { Routes } from '@angular/router';

export const APP_ROUTES: Routes = [
  {
    path: '',
    pathMatch: 'full',
    redirectTo: 'dashboard',
  },
  {
    path: 'dashboard',
    loadComponent: () =>
      import('./features/dashboard/dashboard.component').then(
        (m) => m.DashboardComponent,
      ),
  },
  {
    path: 'audit',
    loadComponent: () =>
      import('./features/audit/audit-list.component').then(
        (m) => m.AuditListComponent,
      ),
  },
  {
    path: 'settings',
    loadComponent: () =>
      import('./features/settings/settings.component').then(
        (m) => m.SettingsComponent,
      ),
  },
  {
    path: 'rules',
    loadComponent: () =>
      import('./features/rules/rules-list.component').then(
        (m) => m.RulesListComponent,
      ),
  },
  {
    path: 'mcp-config',
    loadComponent: () =>
      import('./features/mcp-config/mcp-config.component').then(
        (m) => m.McpConfigComponent,
      ),
  },
  {
    path: 'automation',
    loadComponent: () =>
      import('./features/automation/automation.component').then(
        (m) => m.AutomationComponent,
      ),
  },
  { path: '**', redirectTo: 'dashboard' },
];