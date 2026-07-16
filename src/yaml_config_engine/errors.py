class YamlConfigError(Exception):
    """Base exception."""

class ConfigError(YamlConfigError):
    """Invalid configuration."""

class PathError(YamlConfigError):
    """Path resolution failure."""

class MatchError(YamlConfigError):
    """Selector cardinality failure."""

class OperationError(YamlConfigError):
    """Operation execution failure."""

class ValidationError(YamlConfigError):
    """Validation failure."""
