import json
import logging

import requests
from django.utils.translation import gettext_lazy as _

from fdm.elabftw.models import ElabftwConnection, get_experiment_url

logger = logging.getLogger(__name__)

__all__ = [
    "ElabftwError",
    "get_elabftw_user",
    "search_experiments",
    "get_experiment",
    "get_experiment_details",
]

REQUEST_TIMEOUT_SECONDS = 15


class ElabftwError(Exception):
    pass


def _request(connection: ElabftwConnection, path: str, params: dict | None = None):
    url = f"{connection.url}/api/v2/{path}"

    try:
        response = requests.get(
            url,
            headers={
                "Authorization": connection.api_key,
                "Accept": "application/json",
            },
            params=params,
            timeout=REQUEST_TIMEOUT_SECONDS,
            verify=connection.verify_tls,
        )
    except requests.exceptions.SSLError as e:
        raise ElabftwError(
            _(
                "The certificate of your eLabFTW could not be verified. If it uses an internal certificate, "
                "you can turn off certificate verification in the eLabFTW settings.",
            ),
        ) from e
    except requests.exceptions.RequestException as e:
        logger.info("eLabFTW request to %s failed: %s", url, e)
        raise ElabftwError(_("Your eLabFTW could not be reached. Please check its address.")) from e

    if response.status_code in [401, 403]:
        raise ElabftwError(_("eLabFTW rejected the API key. Please check it in the eLabFTW settings."))

    if response.status_code == 404:
        raise ElabftwError(_("Not found in eLabFTW. Please check the address and that the experiment exists."))

    if response.status_code >= 400:
        raise ElabftwError(_("eLabFTW returned an error ({status}).").format(status=response.status_code))

    try:
        return response.json()
    except ValueError as e:
        raise ElabftwError(_("The address does not seem to point to an eLabFTW instance.")) from e


def _experiment_payload(connection: ElabftwConnection, experiment: dict) -> dict:
    return {
        "id": int(experiment.get("id")),
        "title": experiment.get("title") or "",
        "date": experiment.get("date") or None,
        "category": experiment.get("category_title") or None,
        "status": experiment.get("status_title") or None,
        "owner": experiment.get("fullname") or None,
        "url": get_experiment_url(connection.url, experiment.get("id")),
    }


def get_elabftw_user(connection: ElabftwConnection) -> dict:
    user = _request(connection, "users/me")

    if not isinstance(user, dict):
        raise ElabftwError(_("The address does not seem to point to an eLabFTW instance."))

    return {
        "fullname": user.get("fullname") or " ".join(filter(None, [user.get("firstname"), user.get("lastname")])),
        "email": user.get("email") or None,
    }


def search_experiments(connection: ElabftwConnection, query: str = "", limit: int = 25) -> list[dict]:
    params = {
        "limit": limit,
        "order": "lastchange",
        "sort": "desc",
    }

    query = (query or "").strip()

    # Allow finding an experiment by its ID, e.g. "123" or "#123"
    if query.lstrip("#").isdigit():
        try:
            return [get_experiment(connection, int(query.lstrip("#")))]
        except ElabftwError:
            pass

    if query:
        params["q"] = query

    experiments = _request(connection, "experiments", params=params)

    if not isinstance(experiments, list):
        raise ElabftwError(_("The address does not seem to point to an eLabFTW instance."))

    return [_experiment_payload(connection, experiment) for experiment in experiments if experiment.get("id")]


def get_experiment(connection: ElabftwConnection, experiment_id: int) -> dict:
    experiment = _request(connection, f"experiments/{int(experiment_id)}")

    if not isinstance(experiment, dict) or not experiment.get("id"):
        raise ElabftwError(_("Not found in eLabFTW. Please check the address and that the experiment exists."))

    return _experiment_payload(connection, experiment)


def get_experiment_details(connection: ElabftwConnection, experiment_id: int) -> dict:
    """
    The experiment as documented in eLabFTW, for evaluation exports.
    """
    experiment = _request(connection, f"experiments/{int(experiment_id)}")

    if not isinstance(experiment, dict) or not experiment.get("id"):
        raise ElabftwError(_("Not found in eLabFTW. Please check the address and that the experiment exists."))

    details = _experiment_payload(connection, experiment)

    for key in ["elabid", "created_at", "modified_at", "tags", "body", "metadata", "rating"]:
        if experiment.get(key) not in [None, ""]:
            details[key] = experiment.get(key)

    # eLabFTW stores extra fields as a JSON string
    if isinstance(details.get("metadata"), str):
        try:
            details["metadata"] = json.loads(details["metadata"])
        except ValueError:
            pass

    return details
