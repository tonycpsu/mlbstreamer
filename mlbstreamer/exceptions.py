class MLBPlayException(Exception):
    pass

class MLBPlayInvalidArgumentError(MLBPlayException):
    pass

class StreamSessionException(MLBPlayException):
    pass
