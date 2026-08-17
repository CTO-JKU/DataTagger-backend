# mod_passenger generic passenger_wsgi.py script, Python 3.4 version
# Copyright (C) 2014 ANEXIA Internetdienstleistungs GmbH

import datetime
import os
import sys

THIS_FILE_ABS = os.path.abspath(__file__)
THIS_FILE_DIR = os.path.dirname(THIS_FILE_ABS)
VENV_PYTHON_EXE = os.path.join(THIS_FILE_DIR, "venv", "bin", "python")
ENV_FILE_PATH = os.path.join(THIS_FILE_DIR, "environment")
LOG_PATH = os.path.join(THIS_FILE_DIR, "logs", "passenger_wsgi.log")
APP_DIR = os.path.join(THIS_FILE_DIR, "app")

CONF_ERROR_HTML = """
<html>
  <head>
    <title>Configuration error</title>
  </head>
  <body>
    <h1>Configuration error</h1>

    <p>
      The application is not configured correctly.
      Please check logs/passenger_wsgi.log for details.
    </p>
  </body>
</html>
"""


def error_app(environ, start_response):
    start_response("500", [("Content-Type", "text/html")])
    return CONF_ERROR_HTML


def log(message, *args):
    stat_result = os.stat(THIS_FILE_ABS)
    if not os.path.exists(os.path.dirname(LOG_PATH)):
        os.makedirs(os.path.dirname(LOG_PATH), mode=0o770)
        os.chown(os.path.dirname(LOG_PATH), stat_result.st_uid, stat_result.st_gid)

    message = message % args

    with open(LOG_PATH, "a") as fp:
        fp.write("[%d] %s :: %s\n" % (os.getpid(), str(datetime.datetime.now()), message))
        fp.flush()
    os.chown(LOG_PATH, stat_result.st_uid, stat_result.st_gid)


def initialize():
    # Check if virtualenv Python executable exists
    if not os.path.exists(VENV_PYTHON_EXE):
        # Venv interpreter could not be found. Just bailing out makes it
        # hard to debug the situation, so we are installing a dummy application
        # which servers a 500 page.
        log("Virtualenv Python interpreter at %s is missing.", VENV_PYTHON_EXE)
        return error_app

    stat_result = os.stat(THIS_FILE_ABS)
    if stat_result.st_uid == 0 or stat_result.st_gid == 0:
        log("SECURITY: passenger_wsgi.py must be owned by vhost-user, not root!")
        return error_app

    # Check if we are already running that executable.
    if sys.executable != VENV_PYTHON_EXE:
        # Not running that executable, which means we are not inside the
        # Python venv yet, so let's call execl on that executable.
        # This file will be executed from again, but the check above will not
        # trigger.
        try:
            os.execl(VENV_PYTHON_EXE, VENV_PYTHON_EXE, *sys.argv)
        except OSError as e:
            log("Error spawning VENV_PYTHON_EXE=%s: %s", VENV_PYTHON_EXE, e)
            return error_app

    # Getting this far means the venv is fine and we are now executing from
    # within the venv.
    # Next up is to seed the environment... This is done by 'sourcing' the
    # 'environment' file.
    if os.path.exists(ENV_FILE_PATH):
        with open(ENV_FILE_PATH) as fp:
            for line in fp.readlines():
                line = line.strip()
                # Ignore comments and empty lines.
                if line == "" or line.startswith("#"):
                    continue

                # export is not required, silently strip it off.
                if line.startswith("export "):
                    line = line[len('export '):].strip()  # fmt: skip

                # If we find an equals sign in the line, split the line
                # at that character. The left-hand-side becomes the
                # variable name, whilst the right-hand-side becomes its
                # value.
                if "=" in line:
                    var, value = line.split("=", 1)
                    # Update the environment using the parsed variable
                    # name and value.
                    os.environ[var] = value
                else:
                    log('Syntax error in %s: "%s" is invalid.', ENV_FILE_PATH, line)
                    return error_app

    # Check if the application directory is in place.
    if not os.path.exists(APP_DIR):
        log("Application directory %s is missing.", APP_DIR)
        return error_app

    # Update sys.path accordingly...
    sys.path.insert(0, APP_DIR)

    # Install ErrorMiddleware. This prevents the passenger process from crashing
    # in case of an uncaught exception.
    middleware = None
    try:
        from paste.exceptions.errormiddleware import ErrorMiddleware

        middleware = ErrorMiddleware
    except ImportError as e:
        # ErrorMiddleware is missing.
        log(
            "paste ErrorMiddleware is missing in venv: %s. Please run %s/pip install paste",
            e,
            os.path.dirname(VENV_PYTHON_EXE),
        )
        return error_app

    application = None
    middleware_debug = False
    if "PASSENGER_DEBUG" in os.environ:
        middleware_debug = True

    try:
        from configurations.wsgi import get_wsgi_application

        application = get_wsgi_application()
    except ImportError:
        log("django-configurations is missing in venv. Starting with standard Django configuration.")

        try:
            from django.core.wsgi import get_wsgi_application

            application = get_wsgi_application()
        except ImportError as e:
            log("Could not import Django WSGIHandler: %s", e)
            return error_app

    if middleware and middleware_debug:
        return middleware(application, debug=True)

    # Everything went smoothly, the application should be usable.
    return application


application = initialize()
