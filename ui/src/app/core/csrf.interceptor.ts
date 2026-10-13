import { HttpInterceptorFn, HttpRequest, HttpHandlerFn } from '@angular/common/http';

/**
 * Per-session CSRF token from ``GET /api/csrf-token``.
 *
 * The control-plane session cookie is HttpOnly. This value is only a
 * CSRF header and is not the MCP bearer or the admin secret. The fetch
 * fails closed until the operator opens the one-time login link or
 * posts the admin secret.
 */
let csrfCache: string | null = null;
let pending: Promise<string | null> | null = null;

async function loadCsrf(): Promise<string | null> {
  try {
    const response = await fetch('/api/csrf-token', { credentials: 'include' });
    if (!response.ok) {
      csrfCache = null;
      return null;
    }
    const body = (await response.json()) as { csrf_token?: string };
    csrfCache = body.csrf_token ?? null;
  } catch {
    csrfCache = null;
  }
  return csrfCache;
}

export function refreshCsrf(): Promise<string | null> {
  pending = loadCsrf();
  return pending;
}

export function csrfReady(): Promise<boolean> {
  if (!pending) {
    pending = loadCsrf();
  }
  return pending.then((token) => token !== null);
}

function isUnsafeMethod(method: string): boolean {
  return method !== 'GET' && method !== 'HEAD' && method !== 'OPTIONS';
}

/**
 * Add ``X-LMCP-CSRF`` on unsafe control-plane calls after a session exists.
 */
export const csrfInterceptor: HttpInterceptorFn = (
  req: HttpRequest<unknown>,
  next: HttpHandlerFn,
) => {
  if (!isUnsafeMethod(req.method) || req.url.includes('/api/login')) {
    return next(req);
  }
  const wait = pending ?? refreshCsrf();
  return wait.then((token) => {
    if (!token) return next(req);
    return next(req.clone({ setHeaders: { 'X-LMCP-CSRF': token } }));
  });
};
