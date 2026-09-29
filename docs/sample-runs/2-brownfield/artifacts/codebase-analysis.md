# Codebase analysis (shortener)

## Modules

- `shortener` (11 lines): 
- `shortener.__main__` (15 lines): 
- `shortener.app` (103 lines): create_app
- `shortener.codegen` (28 lines): generate_code, is_valid_alias
- `shortener.config` (43 lines): _int_env, Settings
- `shortener.db` (90 lines): Database
- `shortener.errors` (30 lines): ShortenerError, InvalidUrlError, InvalidAliasError, AliasTakenError, LinkNotFoundError, LinkExpiredError, CodeSpaceExhaustedError
- `shortener.link_service` (70 lines): LinkService
- `shortener.models` (25 lines): utcnow, Link
- `shortener.repository` (60 lines): _to_iso, _from_iso, _row_to_link, LinkRepository
- `shortener.schemas` (28 lines): CreateLinkRequest, LinkResponse, ErrorResponse
- `shortener.validation` (34 lines): validate_url

## Routes

- GET /healthz -> healthz
- GET /readyz -> readyz
- POST /api/v1/links -> create_link
- GET /api/v1/links/{code} -> get_link
- DELETE /api/v1/links/{code} -> delete_link
- GET /{code} -> redirect

## Impacted

- `shortener.app`: defines symbols matching ['redirect']
- `shortener.db`: named in spec
- `shortener.errors`: defines symbols matching ['expired']
- `shortener.link_service`: named in spec

## Blast radius (transitive importers)

- `shortener.__main__`
- `shortener.repository`
- `shortener.validation`

## Tests to re-run

- tests/shortener/conftest.py
- tests/shortener/test_api.py
- tests/shortener/test_config_models.py
- tests/shortener/test_link_service.py
- tests/shortener/test_persistence.py
- tests/shortener/test_units.py
