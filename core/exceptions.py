from rest_framework.exceptions import APIException


class CirculationError(APIException):
    """A business-rule refusal. Rendered as 409 {"code": "...", "detail": "..."} so the UI can react to `code`."""
    status_code = 409

    def __init__(self, code, message):
        super().__init__({"code": code, "detail": message})
