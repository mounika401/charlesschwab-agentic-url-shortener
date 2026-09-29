# URL Shortener API reference

Version 1.2.0. Generated from the application's OpenAPI schema by the docs agent; do not edit by hand.

## POST `/api/v1/links`

Create Link

Body `CreateLinkRequest`: `url`, `custom_alias`, `ttl_seconds`

Responses: 201, 422, 429

## GET `/api/v1/links/{code}`

Get Link

Parameters: `code` (path)

Responses: 200, 422

## DELETE `/api/v1/links/{code}`

Delete Link

Parameters: `code` (path)

Responses: 204, 422

## GET `/api/v1/links/{code}/stats`

Link Stats

Parameters: `code` (path)

Responses: 200, 422

## GET `/healthz`

Healthz

Responses: 200

## GET `/readyz`

Readyz

Responses: 200

## GET `/{code}`

Redirect

Parameters: `code` (path)

Responses: 200, 404, 410, 422, 429
