# Codebase analysis (shortener)

## Modules

- `shortener` (11 lines): 
- `shortener.__main__` (15 lines): 
- `shortener.analytics` (86 lines): ClickContext, LinkStats, visitor_hash, referrer_host, AnalyticsRepository
- `shortener.app` (172 lines): _limiter, _client_key, _enforce, create_app
- `shortener.codegen` (28 lines): generate_code, is_valid_alias
- `shortener.config` (61 lines): _csv_env, _int_env, Settings
- `shortener.db` (107 lines): Database
- `shortener.errors` (40 lines): ShortenerError, InvalidUrlError, InvalidAliasError, AliasTakenError, LinkNotFoundError, LinkExpiredError, CodeSpaceExhaustedError, UnsafeUrlError
- `shortener.link_service` (93 lines): LinkService
- `shortener.models` (25 lines): utcnow, Link
- `shortener.ratelimit` (56 lines): Decision, TokenBucketLimiter
- `shortener.repository` (60 lines): _to_iso, _from_iso, _row_to_link, LinkRepository
- `shortener.safety` (51 lines): UrlSafetyPolicy
- `shortener.schemas` (46 lines): CreateLinkRequest, LinkResponse, ErrorResponse, DailyClicks, ReferrerCount, StatsResponse
- `shortener.validation` (34 lines): validate_url

## Routes

- GET /healthz -> healthz
- GET /readyz -> readyz
- POST /api/v1/links -> create_link
- GET /api/v1/links/{code} -> get_link
- DELETE /api/v1/links/{code} -> delete_link
- GET /api/v1/links/{code}/stats -> link_stats
- GET /{code} -> redirect

## Impacted

- `shortener.app`: named in spec; defines symbols matching ['create']
- `shortener.errors`: defines symbols matching ['rate', 'url']
- `shortener.link_service`: named in spec
- `shortener.safety`: defines symbols matching ['safety', 'url']
- `shortener.schemas`: defines symbols matching ['create']
- `shortener.validation`: named in spec; defines symbols matching ['url', 'validate']

## Blast radius (transitive importers)

- `shortener.__main__`

## Tests to re-run

- tests/shortener/conftest.py
- tests/shortener/test_analytics.py
- tests/shortener/test_api.py
- tests/shortener/test_api_safety.py
- tests/shortener/test_bug101_regression.py
- tests/shortener/test_config_models.py
- tests/shortener/test_link_service.py
- tests/shortener/test_persistence.py
- tests/shortener/test_ratelimit.py
- tests/shortener/test_safety.py
- tests/shortener/test_stats_api.py
- tests/shortener/test_units.py
