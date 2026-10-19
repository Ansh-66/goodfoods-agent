"""The single error type for every rule a request can break."""


class BookingError(Exception):
    """A rule violation the guest or the model can act on.

    `code` is stable and machine-readable (tests and evals assert on it);
    `message` is one sentence the model can relay or use to correct itself.
    """

    def __init__(self, code: str, message: str, **details):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details

    def to_dict(self) -> dict:
        return {"ok": False, "error": self.code, "message": self.message, **self.details}


def is_blank(value) -> bool:
    """Small models often send "" or "null" for an optional argument they mean to omit."""
    return value is None or (isinstance(value, str) and value.strip().lower() in ("", "null", "none"))
