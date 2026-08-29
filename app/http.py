import requests


class VerifiedSession(requests.Session):
    """Requests session that never permits callers to disable TLS verification."""

    def request(self, method, url, **kwargs):
        kwargs["verify"] = True
        return super().request(method, url, **kwargs)


def secure_session_from(existing: requests.Session | None = None) -> VerifiedSession:
    session = VerifiedSession()
    if existing is not None:
        session.headers.clear()
        session.headers.update(existing.headers)
    return session
