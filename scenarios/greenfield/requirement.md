PRODUCT BRIEF: Link shortener (v1)

We need an internal HTTP service that turns long URLs into short links.

- A client POSTs a long URL and gets back a short code and the full short URL.
- Visiting /<code> redirects the browser to the original URL.
- Teams can optionally choose their own vanity code (e.g. /q3-report) instead of a random one.
- Links can optionally expire after a given number of seconds; expired links must stop redirecting.
- We want a click counter per link, and the ability to look a link up and delete it.
- Must expose health and readiness endpoints for the platform team.
- Only web links should be accepted (no javascript: or file: links).
- Short codes must not be guessable in sequence.
- Target: redirect handling under 50 ms p99 on a single node for 100 requests per second.
- Data must survive a restart. No external database for v1 - it has to run on a laptop.
