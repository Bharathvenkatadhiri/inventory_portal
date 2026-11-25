"""Errors the API answers with 409 and a `code` the web app acts on
(frontend/packages/api reloads the tab). Kept apart from core/api.py so
apps can raise them without importing the API object."""


class SessionConflict(Exception):
    """The tab's idea of who is signed in, or which organization it's
    working in, no longer matches the shared browser session."""
    code = 'session'

    def __init__(self, message):
        super().__init__(message)
        self.message = message


class SignedInAsSomeoneElse(SessionConflict):
    code = 'user'

    def __init__(self):
        super().__init__("You signed in as a different person in another tab. This tab will reload.")
