"""Exception hierarchy. Catch the most specific class you can handle."""


class PhotoProofError(Exception):
    """Base class for all PhotoProof errors."""


class ConfigurationError(PhotoProofError):
    """Configuration is missing, malformed, or violates policy."""


class IdentityError(ConfigurationError):
    """``app.toml`` identity is inconsistent with the package layout."""


class PolicyError(PhotoProofError):
    """A requested action is not permitted by security or plugin policy."""


class RegistryError(PhotoProofError):
    """The plugin set is inconsistent (e.g. duplicate identifiers)."""


class PlanningError(PhotoProofError):
    """The plugin dependency graph is invalid and cannot be scheduled."""


class InvalidTransitionError(PhotoProofError):
    """An execution state transition is not permitted by the state machine."""


class PluginLoadError(PhotoProofError):
    """A plugin's implementation or configuration schema could not be loaded."""


class NotApplicableError(PhotoProofError):
    """Raised by a plugin to declare it does not apply to the current input."""


class StorageError(PhotoProofError):
    """Runtime storage operation failed or detected an integrity problem."""


class UnsafePathError(StorageError):
    """A path escapes its permitted root or is otherwise unsafe."""


class DatabaseError(PhotoProofError):
    """Persistent state could not be read, written or migrated."""


class EventDeliveryError(PhotoProofError):
    """A critical event subscriber failed; the operation must not continue silently."""


class InputRejectedError(PhotoProofError):
    """An input violated an input policy (size, type, location)."""
