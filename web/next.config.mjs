/**
 * Security headers and build configuration.
 *
 * The CSP is worth reading. This app serves financial figures with clickable
 * provenance to sec.gov, and the realistic injection risk is a stored value —
 * a company name, a rejection reason, a user's watchlist note — rendering as
 * markup. React escapes by default, so CSP is the second layer: even if
 * something slips through, there is no origin it may load a script from.
 *
 * `'unsafe-inline'` is present for styles only, because Next injects inline
 * style attributes. It is deliberately NOT present for scripts.
 */

const csp = [
  "default-src 'self'",
  // No third-party scripts at all. There is no analytics, no tag manager and
  // no font CDN, which is why this can be strict.
  "script-src 'self'",
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data:",
  "font-src 'self'",
  // The app talks only to itself. Provenance links are navigations, not
  // fetches, so sec.gov does not belong here.
  "connect-src 'self'",
  "form-action 'self'",
  "frame-ancestors 'none'",
  "base-uri 'none'",
  "object-src 'none'",
  "upgrade-insecure-requests",
].join("; ");

const headers = [
  { key: "Content-Security-Policy", value: csp },
  // Clickjacking. `frame-ancestors` above is the modern control; this is the
  // fallback for anything that does not honour CSP.
  { key: "X-Frame-Options", value: "DENY" },
  { key: "X-Content-Type-Options", value: "nosniff" },
  // Provenance links go to sec.gov. Sending our full URL — which may carry a
  // screen's criteria — to a third party is needless leakage.
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  // Nothing here needs a camera, a microphone or a location.
  { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
  // Two years, subdomains included. Only meaningful once deployed over HTTPS;
  // harmless on localhost because browsers ignore HSTS from http origins.
  { key: "Strict-Transport-Security", value: "max-age=63072000; includeSubDomains" },
];

/** @type {import('next').NextConfig} */
export default {
  // Pin the workspace root. Without this Next walks up looking for a lockfile
  // and can settle on one in a parent directory — on this machine it picked
  // `~/package-lock.json`, which belongs to something else entirely. That
  // changes which files get traced for the production bundle, so it is a
  // correctness issue and not only a warning.
  outputFileTracingRoot: import.meta.dirname,
  poweredByHeader: false,   // no free version disclosure
  async headers() {
    return [{ source: "/:path*", headers }];
  },
};
