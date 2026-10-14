"""What the domain can object to. The API turns each kind into a status code."""


class DomainError(Exception):
    pass


class NotFoundError(DomainError):
    """Something that was asked for does not exist."""


class ConflictError(DomainError):
    """The request is valid, but clashes with the current state of things."""


class RuleViolationError(DomainError):
    """The request breaks a business rule."""
