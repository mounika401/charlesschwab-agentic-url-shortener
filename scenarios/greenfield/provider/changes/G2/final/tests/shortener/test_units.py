import pytest

from shortener.codegen import ALPHABET, generate_code, is_valid_alias
from shortener.errors import InvalidUrlError
from shortener.validation import validate_url


def test_generated_codes_use_alphabet_and_length():
    for _ in range(200):
        code = generate_code(7)
        assert len(code) == 7
        assert set(code) <= set(ALPHABET)


def test_generated_codes_are_not_trivially_repeated():
    assert len({generate_code(7) for _ in range(1000)}) == 1000


def test_code_length_lower_bound():
    with pytest.raises(ValueError):
        generate_code(3)


@pytest.mark.parametrize("alias", ["abc", "my-link", "A_1-b", "x" * 32])
def test_valid_aliases(alias):
    assert is_valid_alias(alias)


@pytest.mark.parametrize("alias", ["ab", "-abc", "has space", "x" * 33, "api", "HEALTHZ", "semi;colon"])
def test_invalid_aliases(alias):
    assert not is_valid_alias(alias)


def test_validate_url_normalises_scheme_and_host_only():
    assert validate_url("HTTPS://Example.COM/Path?Q=1") == "https://example.com/Path?Q=1"


@pytest.mark.parametrize(
    "url",
    ["", "   ", "ftp://example.com", "javascript:alert(1)", "data:text/html,hi", "https://", "example.com",
     "https://exa mple.com", "https://example.com/" + "a" * 3000],
)
def test_validate_url_rejects(url):
    with pytest.raises(InvalidUrlError):
        validate_url(url)
