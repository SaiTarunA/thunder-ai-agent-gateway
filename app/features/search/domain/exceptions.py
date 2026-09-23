class SearchError(Exception):
    """Base exception for the search module."""


class InvalidSearchRequest(SearchError):
    """Raised when the search request is invalid."""


class EmptySearchQuery(InvalidSearchRequest):
    """Raised when the search query is empty."""


class SearchAuthorizationError(SearchError):
    """Raised when a search authorization operation fails."""


class SearchSessionNotFound(SearchError):
    """Raised when a search session cannot be found."""


class SearchSessionAccessDenied(SearchError):
    """Raised when a user tries to access another user's search session."""