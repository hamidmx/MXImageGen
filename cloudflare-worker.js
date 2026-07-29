/**
 * Cloudflare Worker — CORS proxy for the Ideogram API.
 *
 * Ideogram's API does not send Access-Control-Allow-Origin, so a browser
 * fetch() from a static page (GitHub Pages, file://) is blocked. This Worker
 * forwards the request server-side and adds the CORS headers on the way back.
 * It is the hosted equivalent of cors-proxy.py, so the app works online
 * without anything running on your machine.
 *
 * ---------------------------------------------------------------------------
 * DEPLOY (free tier, ~3 minutes, no CLI needed)
 * ---------------------------------------------------------------------------
 *  1. Sign in at https://dash.cloudflare.com  (free account is enough)
 *  2. Compute  ->  Workers & Pages  ->  Create  ->  Workers  ->  Start with Hello World
 *  3. Name it e.g.  mx-ideogram-proxy   ->  Deploy
 *  4. Click "Edit code", replace ALL of the sample with this file, then Deploy
 *  5. Copy the URL it gives you, e.g.
 *       https://mx-ideogram-proxy.<your-subdomain>.workers.dev
 *  6. In the app:  ☰ -> Settings -> "Ideogram Proxy URL"  ->  paste it -> Save
 *
 * ---------------------------------------------------------------------------
 * SECURITY
 * ---------------------------------------------------------------------------
 * Your Ideogram API key is NOT stored here. The browser sends it per-request
 * in the Api-Key header and this Worker just relays it. Nothing is logged or
 * persisted.
 *
 * By default any origin may call this Worker. Because a caller must supply
 * their own Ideogram key for a request to succeed, an unknown caller cannot
 * spend your credits. If you still want to lock it down, put your GitHub
 * Pages origin in ALLOWED_ORIGINS below.
 */

// Empty array = allow any origin. To restrict, list exact origins, e.g.
//   const ALLOWED_ORIGINS = ["https://hamidmx.github.io"];
const ALLOWED_ORIGINS = [];

// Only these hosts may be proxied — prevents the Worker being used as an
// open relay to arbitrary sites.
const ALLOWED_HOSTS = [
  "api.ideogram.ai",
  "ideogram.ai"
];

// Ideogram returns generated images on its CDN; allow those through too.
function hostAllowed(hostname) {
  const h = String(hostname || "").toLowerCase();
  return ALLOWED_HOSTS.some(allowed => h === allowed || h.endsWith("." + allowed));
}

function pickOrigin(request) {
  const origin = request.headers.get("Origin") || "*";
  if (ALLOWED_ORIGINS.length === 0) return origin === "null" ? "*" : origin;
  return ALLOWED_ORIGINS.includes(origin) ? origin : ALLOWED_ORIGINS[0];
}

function corsHeaders(request) {
  return {
    "Access-Control-Allow-Origin": pickOrigin(request),
    "Access-Control-Allow-Methods": "GET, POST, PUT, PATCH, DELETE, OPTIONS",
    "Access-Control-Allow-Headers": "Api-Key, Authorization, Content-Type, X-Api-Key",
    "Access-Control-Expose-Headers": "Retry-After",
    "Access-Control-Max-Age": "86400",
    "Vary": "Origin"
  };
}

function jsonError(request, status, message) {
  return new Response(JSON.stringify({ proxy_error: message }), {
    status,
    headers: { ...corsHeaders(request), "Content-Type": "application/json" }
  });
}

export default {
  async fetch(request) {
    // CORS preflight
    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: corsHeaders(request) });
    }

    const url = new URL(request.url);

    // Health check so you can confirm the deploy in a browser tab.
    if (url.pathname === "/" || url.pathname === "") {
      return new Response(
        "Ideogram CORS proxy is running.\n\nUse: /proxy?url=<url-encoded-target>\n",
        { status: 200, headers: { ...corsHeaders(request), "Content-Type": "text/plain" } }
      );
    }

    if (url.pathname !== "/proxy") {
      return jsonError(request, 404, "Path must be /proxy?url=<http(s)-url>");
    }

    const target = url.searchParams.get("url");
    if (!target) {
      return jsonError(request, 400, "Missing ?url= parameter");
    }

    let targetUrl;
    try {
      targetUrl = new URL(target);
    } catch {
      return jsonError(request, 400, "Malformed ?url= parameter");
    }
    if (targetUrl.protocol !== "https:" && targetUrl.protocol !== "http:") {
      return jsonError(request, 400, "Only http(s) targets are allowed");
    }
    if (!hostAllowed(targetUrl.hostname)) {
      return jsonError(request, 403, `Host not allowed: ${targetUrl.hostname}`);
    }

    // Rebuild the outbound request. Hop-by-hop and origin-identifying headers
    // are dropped; Api-Key / Authorization / Content-Type pass through so the
    // multipart boundary survives intact.
    const drop = new Set([
      "host", "origin", "referer", "connection", "cookie",
      "content-length", "accept-encoding",
      "cf-connecting-ip", "cf-ipcountry", "cf-ray", "cf-visitor",
      "x-forwarded-for", "x-forwarded-proto", "x-real-ip"
    ]);
    const headers = new Headers();
    for (const [k, v] of request.headers) {
      if (!drop.has(k.toLowerCase())) headers.set(k, v);
    }

    let upstream;
    try {
      upstream = await fetch(targetUrl.toString(), {
        method: request.method,
        headers,
        body: (request.method === "GET" || request.method === "HEAD") ? undefined : request.body,
        redirect: "follow"
      });
    } catch (e) {
      return jsonError(request, 502, "Upstream fetch failed: " + (e && e.message ? e.message : String(e)));
    }

    // Relay the response, swapping in our CORS headers. Content-Type is kept
    // so JSON stays JSON and image bytes stay images.
    const out = new Headers(corsHeaders(request));
    const ct = upstream.headers.get("content-type");
    if (ct) out.set("Content-Type", ct);
    const ra = upstream.headers.get("retry-after");
    if (ra) out.set("Retry-After", ra);

    return new Response(upstream.body, { status: upstream.status, headers: out });
  }
};
