"""VDI transactional email (Resend)."""

__all__ = ["ResendMailError", "send_access_code_email", "send_raw_email"]


def __getattr__(name: str):
    if name in __all__:
        from . import resend_mail as _m
        return getattr(_m, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
