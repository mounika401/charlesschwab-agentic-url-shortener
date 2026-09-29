"""Domain errors. The HTTP layer maps each to a status code in one place."""


class ShortenerError(Exception):
    """Base class for expected, client-visible failures."""


class InvalidUrlError(ShortenerError):
    pass


class InvalidAliasError(ShortenerError):
    pass


class AliasTakenError(ShortenerError):
    pass


class LinkNotFoundError(ShortenerError):
    pass


class LinkExpiredError(ShortenerError):
    pass


class CodeSpaceExhaustedError(ShortenerError):
    """Random code allocation kept colliding; signals the code length is too small."""


class UnsafeUrlError(ShortenerError):
    """Well-formed URL that policy forbids publishing (internal hosts, credentials, blocklist)."""


class RateLimitedError(ShortenerError):
    def __init__(self, retry_after_seconds: float) -> None:
        super().__init__(f"rate limit exceeded; retry after {retry_after_seconds:.1f}s")
        self.retry_after_seconds = retry_after_seconds
