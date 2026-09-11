/**
 * Session cookie handling.
 *
 * Four flags, each doing a specific job:
 *
 *  - `httpOnly` so a successful XSS cannot read the session token. This is the
 *    one that matters most, because it limits the damage of the failure the
 *    CSP is also guarding against.
 *  - `secure` so the token never crosses plain HTTP. Relaxed on localhost
 *    only, because a dev server has no certificate.
 *  - `sameSite: 'lax'` so the cookie is not sent on cross-site POSTs. With no
 *    state-changing GET endpoints, this is most of CSRF protection; `strict`
 *    would break the magic-link flow, since arriving from an email client is
 *    a cross-site navigation.
 *  - `path: '/'` because the session applies to the whole app.
 *
 * The token itself is opaque and carries no claims. A JWT would let the server
 * skip a database lookup and would also make revocation hard — and a platform
 * holding broker credentials in Phase 21 should be able to end a session
 * immediately.
 */

export const SESSION_COOKIE = "ii_session";

const isProduction = process.env.NODE_ENV === "production";

export function sessionCookieOptions(maxAgeSeconds: number) {
  return {
    httpOnly: true,
    secure: isProduction,
    sameSite: "lax" as const,
    path: "/",
    maxAge: maxAgeSeconds,
  };
}

export const clearedSessionCookie = {
  ...sessionCookieOptions(0),
  maxAge: 0,
};

/**
 * The client identifier used for rate limiting.
 *
 * `x-forwarded-for` is only trustworthy behind a proxy that sets it, which
 * Vercel does. Taking the FIRST entry is deliberate: later entries are
 * attacker-controlled, because anything can append to the header on the way
 * in.
 */
export function clientIdentifier(headers: Headers): string {
  const forwarded = headers.get("x-forwarded-for");
  if (forwarded) return forwarded.split(",")[0].trim();
  return headers.get("x-real-ip") ?? "unknown";
}
