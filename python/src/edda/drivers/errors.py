"""Errors shared by the importers."""


class DriverError(ValueError):
    """A file or page cannot be imported as given."""


class NotAllowed(DriverError):
    """The policy does not allow fetching from that address."""


class FetchFailed(DriverError):
    """The address could not be fetched."""
