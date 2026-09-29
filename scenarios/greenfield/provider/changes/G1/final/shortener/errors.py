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
