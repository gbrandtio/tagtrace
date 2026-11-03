"""The errors tagtrace raises on purpose. Their messages are shown to the user as they are."""


class TagtraceError(Exception):
    """A failure the user can act on."""


class ConfigError(TagtraceError):
    """The config or an input file is wrong, or an optional extra is missing."""


class GitError(TagtraceError):
    """A git command failed for one repository."""


class JiraAuthError(TagtraceError):
    """Jira did not accept the sign-in. The whole run stops, because every later call would fail too."""


class LLMError(TagtraceError):
    """A model call failed or returned unusable output."""
