from http import HTTPStatus


class ServiceError(Exception):
    status_code = HTTPStatus.BAD_REQUEST

    def __init__(self, message: str, *, status_code: int | HTTPStatus | None = None):
        super().__init__(message)
        self.message = message
        if status_code is not None:
            self.status_code = HTTPStatus(status_code)


class NotFoundError(ServiceError):
    status_code = HTTPStatus.NOT_FOUND


class ConflictError(ServiceError):
    status_code = HTTPStatus.CONFLICT


class CalibreUnavailableError(ServiceError):
    status_code = HTTPStatus.SERVICE_UNAVAILABLE
