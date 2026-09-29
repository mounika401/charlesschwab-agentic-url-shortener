import pytest

from shortener.errors import UnsafeUrlError
from shortener.safety import UrlSafetyPolicy

POLICY = UrlSafetyPolicy(blocked_domains=("evil.test",), own_host="sho.rt")


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/path",
        "https://sub.example.com:8443/x?y=1",
        "https://8.8.8.8/",
        "https://[2001:4860:4860::8888]/",
        "https://notevil.test/",  # suffix match must respect label boundaries
    ],
)
def test_allowed_destinations(url):
    POLICY.check(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://trusted.com@evil.example/",  # credential-based deception
        "https://user:pw@example.com/",
        "http://localhost:8080/admin",
        "http://printer.local/",
        "http://metadata.google.internal/",
        "http://127.0.0.1/",
        "http://10.0.0.5/",
        "http://192.168.1.1/",
        "http://169.254.169.254/latest/meta-data/",  # cloud metadata endpoint
        "http://[::1]/",
        "http://0.0.0.0/",
        "https://evil.test/",
        "https://login.evil.test/",
        "https://sho.rt/abc",  # redirect loop via our own domain
    ],
)
def test_blocked_destinations(url):
    with pytest.raises(UnsafeUrlError):
        POLICY.check(url)
