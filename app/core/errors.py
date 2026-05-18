# TODO: Alle Custom Exceptions aus docs/error-recovery-system.md

class SocialAIError(Exception):
    """Basis-Exception."""
    pass


class InvalidStatusTransitionError(SocialAIError):
    pass


class SlackVerificationError(SocialAIError):
    pass


class UnauthorizedUserError(SocialAIError):
    pass


class DriveUploadError(SocialAIError):
    pass


class SheetsWriteError(SocialAIError):
    pass


class SlackPostError(SocialAIError):
    pass


class JobNotFoundError(SocialAIError):
    pass


class ConfigurationError(SocialAIError):
    pass
