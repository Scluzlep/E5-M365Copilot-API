"""The User-Agent and Client Hints presented across HTTP requests and browser sessions.

Keeping the string here (imported by both :mod:`copilot.driver` and
:mod:`copilot.browser`) ensures consistent client presentation.

Why these exact values:

* ``CHROME_UA`` is a standard desktop **Windows** Chrome UA. We standardise on the
  same major version Playwright actually bundles, so overriding a launched
  Chromium's UA to this string does *not* contradict the browser's native
  ``Sec-CH-UA`` client hint.
* ``IMPERSONATE_TARGET`` pins curl_cffi to a fixed TLS/HTTP2 fingerprint.
"""

# Real desktop Windows Chrome. Must match Playwright's bundled Chromium major
# version (currently 148) so the UA override introduces no client-hint conflict.
CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
)

# Client hints that must accompany CHROME_UA so the platform/version a server
# reads from the hints agrees with the UA line. Used by the curl_cffi driver,
# which otherwise emits the impersonation profile's native (macOS) hints.
CHROME_CLIENT_HINTS = {
    "sec-ch-ua-platform": '"Windows"',
    "sec-ch-ua": '"Google Chrome";v="148", "Chromium";v="148", "Not_A Brand";v="24"',
}

# Pinned curl_cffi impersonation profile (TLS/HTTP2 fingerprint). Closest stable
# profile to CHROME_UA's version; the UA itself is overridden on top.
IMPERSONATE_TARGET = "chrome146"

# Standardized US locale and timezone for spoofing E5 / E3 / Business Chat requests.
# Must be consistent across Playwright browser launches and HTTP requests to prevent
# region-based telemetry rejection or fingerprint mismatch.
US_LOCALE = "en-US"
US_TIMEZONE = "America/Los_Angeles"
US_TIMEZONE_OFFSET = -8  # PST offset (America/Los_Angeles)
US_ACCEPT_LANGUAGE = "en-US,en;q=0.9"

