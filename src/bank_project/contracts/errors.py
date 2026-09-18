class FeatureNotImplemented(Exception):
    """A reserved module was called before its business implementation exists."""


class IntakeError(Exception):
    """A safe, actionable error; never include row values or upstream credentials."""

    status_code = 422


class Conflict(IntakeError):
    status_code = 409


class NotFound(IntakeError):
    status_code = 404


class LimitExceeded(IntakeError):
    status_code = 413


class DependencyUnavailable(IntakeError):
    status_code = 503
