import { HttpInterceptorFn, HttpRequest, HttpHandlerFn } from '@angular/common/http';

/**
 * Cache for the CSRF token returned by ``/api/csrf-token``.
 *
 * On bootstrap the SPA fetches the token once; the cookie set by the
 * server is HttpOnly + SameSite=Lax, so the SPA only ever sees the
 * token via this in-memory cache. The cache is cleared when the page
 * reloads so a server restart invalidates it.
 */
let csrfCache: string | null = null;
let bootstrapped = false;
const bootstrapPromise: Promise<string | null> = bootstrapCsrf();

async function bootstrapCsrf(): Promise<string | null> {
  if (bootstrapped) return csrfCache;
  bootstrapped = true;
  try {
    const response = await fetch('/api/csrf-token', { credentials: 'include' });
    if (!response.ok) return null;
    const body = (await response.json()) as { csrf_token?: string };
    csrfCache = body.csrf_token ?? null;
  } catch {
    csrfCache = null;
  }
  return csrfCache;
}

function isUnsafeMethod(method: string): boolean {
  return method !== 'GET' && method !== 'HEAD' && method !== 'OPTIONS';
}

/**
 * Angular HTTP interceptor that adds ``X-LMCP-CSRF`` on every unsafe
 * request once the bootstrap token has been fetched.
 */
export const csrfInterceptor: HttpInterceptorFn = (
  req: HttpRequest<unknown>,
  next: HttpHandlerFn,
) => {
  if (!isUnsafeMethod(req.method)) {
    return next(req);
  }
  return bootstrapPromise.then((token) => {
    if (!token) return next(req);
    return next(
      req.clone({ setHeaders: { 'X-LMCP-CSRF': token } }),
    );
  });
};