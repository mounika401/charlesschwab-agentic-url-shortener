"""URL shortener service.

Layers (outer -> inner):
    app.py          HTTP transport (FastAPI), request/response mapping
    link_service.py business rules (validation, code allocation, expiry)
    repository.py   persistence (SQLite), no business logic
    db.py           connection management + versioned schema migrations
"""

__version__ = "1.2.0"
