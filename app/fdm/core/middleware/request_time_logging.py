import datetime
import logging
import uuid

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import connection
from django.utils.deprecation import MiddlewareMixin
from django_userforeignkey.request import get_current_user

User = get_user_model()

request_logger = logging.getLogger("django.middleware.request_time_logging")
MAX_DEBUG_SQL_LENGTH = 10000

__all__ = [
    "RequestTimeLoggingMiddleware",
]


# original sources from:
# https://gist.github.com/j4mie/956843
# and
# https://djangosnippets.org/snippets/1826/
class RequestTimeLoggingMiddleware(MiddlewareMixin):
    """Middleware class logging request time to stderr.

    This class can be used to measure time of request processing
    within Django.  It can be also used to log time spent in
    middleware and in view itself, by putting middleware multiple
    times in INSTALLED_MIDDLEWARE.

    Static method `log_message` may be used independently of the
    middleware itself, outside of it, and even when middleware is not
    listed in INSTALLED_MIDDLEWARE.
    """

    @staticmethod
    def log_message(request, tag, message=""):
        """Log timing message to stderr.

        Logs message about `request` with a `tag` (a string, 10
        characters or less if possible), timing info and optional
        `message`.

        Log format is "timestamp tag uuid count path +delta message"

        - timestamp is microsecond timestamp of message
        - tag is the `tag` parameter
        - uuid is the UUID identifying request
        - count is number of logged message for this request
        - path is request.path
        - delta is timedelta between first logged message for this request and current message
        - message is the `message` parameter.
        """

        dt = datetime.datetime.utcnow()
        if not hasattr(request, "_logging_uuid"):
            request._logging_uuid = uuid.uuid1()
            request._logging_start_dt = dt
            request._logging_pass = 0

        request._logging_pass += 1
        request_logger.debug(
            "%s, %s, %s, %d, %s, %s, %0.4f seconds, %s"
            % (
                dt.isoformat(),
                tag,
                request._logging_uuid,
                request._logging_pass,
                request.method,
                request.path,
                (dt - request._logging_start_dt).total_seconds(),
                message,
            ),
        )

    def process_request(self, request):
        self.log_message(request, "request ")

    def process_response(self, request, response):
        s = getattr(response, "status_code", 0)
        r = f"by {str(get_current_user())}, status {str(s)}, "
        if s in (300, 301, 302, 307):
            r += " redirect to %s" % response.get("Location", "?")
        elif hasattr(response, "content") and response.content:
            r += " sent %d bytes" % len(response.content)
        elif hasattr(response, "streaming_content") and response.streaming_content:
            r += " streaming / downloading"

        # if status code is 2xx and debug mode is activated
        if 200 <= s <= 299 and settings.DEBUG:
            try:
                queries = list(connection.queries)
            except Exception as exc:
                request_logger.warning(
                    "Skipping debug query aggregation for %s because query inspection failed: %s",
                    request.path,
                    exc,
                )
            else:
                total_time = 0
                oversized_queries = 0

                for query in queries:
                    sql = query.get("sql", "")
                    if isinstance(sql, str) and len(sql) > MAX_DEBUG_SQL_LENGTH:
                        oversized_queries += 1

                    query_time = query.get("time")
                    if query_time is None:
                        # django-debug-toolbar monkeypatches the connection
                        # cursor wrapper and adds extra information in each
                        # item in connection.queries. The query time is stored
                        # under the key "duration" rather than "time" and is
                        # in milliseconds, not seconds.
                        query_time = query.get("duration", 0) / 1000
                    total_time += float(query_time)

                r += ", %d queries, %0.4f seconds" % (len(queries), total_time)
                if oversized_queries:
                    r += ", %d oversized SQL statements skipped" % oversized_queries

        self.log_message(request, "response", r)
        return response
