"""Safe public API failures shared across application routers."""


class APIError(Exception):
    """A stable code and localized explanation without backend secrets."""

    def __init__(self, status: int, code: str, message: str) -> None:
        self.status, self.code, self.message = status, code, message


def fail(status: int, code: str, message: str) -> None:
    """Reject a request with a safe public explanation."""
    raise APIError(status, code, message)
