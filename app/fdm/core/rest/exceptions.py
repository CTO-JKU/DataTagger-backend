from rest_framework import views

__all__ = [
    "error_handler",
]


def error_handler(exc, context):
    # Call REST framework's default exception handler first,
    # to get the standard error response.
    response = views.exception_handler(exc, context)

    # Now add the status fields to the response.
    if response is not None:
        if not isinstance(response.data, dict):
            response.data = {
                "detail": response.data,
            }
        response.data["success"] = False
        response.data["status_code"] = getattr(exc, "status_code", None) or response.status_code

    return response
