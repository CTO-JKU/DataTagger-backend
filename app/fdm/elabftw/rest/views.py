from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import mixins, serializers, status
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from fdm.core.rest.base import BaseGenericViewSet, BaseModelViewSet
from fdm.elabftw.exports import datasets_for_link
from fdm.elabftw.models import ElabftwConnection, ExperimentLink, normalize_elabftw_url
from fdm.elabftw.permissions import (
    can_edit_link_target,
    can_view_link_target,
    get_effective_links,
    get_link_target,
    get_viewable_experiment_links,
)
from fdm.elabftw.rest.serializers import *
from fdm.elabftw.services import ElabftwError, get_elabftw_user, get_experiment, search_experiments
from fdm.folders.models import Folder, Subfolder

__all__ = [
    "ElabftwViewSet",
    "ExperimentLinkViewSet",
]


def _get_connection(user) -> ElabftwConnection:
    connection = ElabftwConnection.objects.filter(user=user).first()

    if not connection:
        raise NotFound(_("Please connect your eLabFTW account first."))

    return connection


def _link_place(link: ExperimentLink, target) -> dict:
    """
    Where a link was set, as a readable path like "Project / NMR / FH-027 / 5".
    """
    if isinstance(target, Folder):
        folder, subfolder, names = target, None, []
    elif isinstance(target, Subfolder):
        folder, subfolder = target.folder, target
        names = [item.name for item in target.get_path()]
    else:
        folder, subfolder = target.folder, target.subfolder
        names = [*(item.name for item in target.subfolder.get_path())] if target.subfolder else []
        names.append(target.name or "")

    prefix = [folder.project.name, folder.name] if folder else ["My draft files"]

    return {
        "link_id": link.pk,
        "target_type": link.target_type,
        "target_id": target.pk,
        "name": target.name or "",
        "path": " / ".join([*prefix, *names]),
        "project_id": folder.project_id if folder else None,
        "folder_id": folder.pk if folder else None,
        "subfolder_id": subfolder.pk if subfolder else None,
    }


def _elabftw_error_response(error: ElabftwError) -> Response:
    return Response(
        data={
            "error": str(error),
        },
        status=status.HTTP_502_BAD_GATEWAY,
    )


class ElabftwViewSet(BaseGenericViewSet):
    """
    The eLabFTW connection of the current user and the experiments it gives access to.
    """

    permission_classes = [
        IsAuthenticated,
    ]

    serializer_class = ElabftwConnectionSerializer

    throttle_classes = []

    pagination_class = None

    filter_backends = []

    def get_queryset(self):
        return ElabftwConnection.objects.filter(user=self.request.user)

    @extend_schema(
        responses={
            200: ElabftwConnectionSerializer,
        },
        methods=["GET"],
    )
    @extend_schema(
        request=ElabftwConnectionSerializer,
        responses={
            200: ElabftwConnectionSerializer,
        },
        methods=["PUT"],
    )
    @extend_schema(
        responses={
            204: None,
        },
        methods=["DELETE"],
    )
    @action(
        detail=False,
        methods=["GET", "PUT", "DELETE"],
        url_path="connection",
        url_name="connection",
    )
    def connection(self, request, *args, **kwargs):
        instance = ElabftwConnection.objects.filter(user=request.user).first()

        if request.method == "GET":
            if not instance:
                raise NotFound(_("No eLabFTW account has been connected yet."))

            return Response(ElabftwConnectionSerializer(instance).data)

        if request.method == "DELETE":
            if instance:
                instance.delete()

            return Response(status=status.HTTP_204_NO_CONTENT)

        serializer = ElabftwConnectionSerializer(instance, data=request.data, partial=bool(instance))
        serializer.is_valid(raise_exception=True)

        try:
            instance = serializer.save(user=request.user)
        except ValidationError as e:
            raise serializers.ValidationError(e.message_dict if hasattr(e, "error_dict") else e.messages) from e

        return Response(ElabftwConnectionSerializer(instance).data)

    @extend_schema(
        request=None,
        responses={
            200: ElabftwConnectionTestSerializer,
        },
    )
    @action(
        detail=False,
        methods=["POST"],
        url_path="connection/test",
        url_name="connection-test",
    )
    def test_connection(self, request, *args, **kwargs):
        try:
            elabftw_user = get_elabftw_user(_get_connection(request.user))
        except ElabftwError as e:
            return _elabftw_error_response(e)

        return Response(ElabftwConnectionTestSerializer(elabftw_user).data)

    @extend_schema(
        parameters=[
            OpenApiParameter("search", str, description="Search term or experiment ID"),
        ],
        responses={
            200: ElabftwExperimentSerializer(many=True),
        },
    )
    @action(
        detail=False,
        methods=["GET"],
        url_path="experiments",
        url_name="experiments",
    )
    def experiments(self, request, *args, **kwargs):
        try:
            experiments = search_experiments(_get_connection(request.user), request.query_params.get("search", ""))
        except ElabftwError as e:
            return _elabftw_error_response(e)

        return Response(ElabftwExperimentSerializer(experiments, many=True).data)


@extend_schema(
    parameters=[
        OpenApiParameter("ordering", exclude=True),
        OpenApiParameter("search", exclude=True),
    ],
)
class ExperimentLinkViewSet(
    BaseModelViewSet,
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    mixins.DestroyModelMixin,
):
    queryset = ExperimentLink.objects.none()

    serializer_class = ExperimentLinkSerializer

    permission_classes = [
        IsAuthenticated,
    ]

    throttle_classes = []

    pagination_class = None

    filter_backends = []

    filterset_class = None

    def get_queryset(self):
        return ExperimentLink.objects.all()

    @extend_schema(
        parameters=[
            OpenApiParameter("target_type", str, required=True, enum=ExperimentLink.TargetType.values),
            OpenApiParameter("target_id", str, required=True),
        ],
    )
    def list(self, request, *args, **kwargs):
        target_type = request.query_params.get("target_type")
        target_id = request.query_params.get("target_id")
        target = get_link_target(target_type, target_id)

        if not can_view_link_target(request.user, target):
            return Response([])

        links = get_effective_links(target_type, target)

        return Response(ExperimentLinkSerializer(links, many=True).data)

    @extend_schema(
        responses={
            200: LinkedExperimentSerializer(many=True),
        },
    )
    @action(
        detail=False,
        methods=["GET"],
        url_path="experiments",
        url_name="experiments",
    )
    def experiments(self, request, *args, **kwargs):
        """
        All eLabFTW experiments linked anywhere the user has access to, with where they are linked and how many files
        they cover (including files inside linked folders and subfolders).
        """
        experiments = {}

        for link in get_viewable_experiment_links(request.user).order_by("creation_date"):
            target = get_link_target(link.target_type, link.target_id)
            if not target:
                continue

            key = (normalize_elabftw_url(link.elabftw_url), link.experiment_id)
            experiment = experiments.setdefault(
                key,
                {
                    "elabftw_url": key[0],
                    "experiment_id": link.experiment_id,
                    "title": link.title,
                    "experiment_url": link.experiment_url,
                    "link_ids": [],
                    "places": [],
                    "dataset_ids": set(),
                },
            )
            experiment["link_ids"].append(link.pk)
            experiment["places"].append(_link_place(link, target))
            experiment["dataset_ids"].update(dataset.pk for dataset in datasets_for_link(link))

        data = [
            {**experiment, "files_count": len(experiment.pop("dataset_ids"))}
            for experiment in sorted(experiments.values(), key=lambda item: -item["experiment_id"])
        ]

        return Response(LinkedExperimentSerializer(data, many=True).data)

    @extend_schema(
        request=ExperimentLinkCreateSerializer,
        responses={
            201: ExperimentLinkSerializer,
        },
    )
    def create(self, request, *args, **kwargs):
        serializer = ExperimentLinkCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        target_type = serializer.validated_data["target_type"]
        target = get_link_target(target_type, serializer.validated_data["target_id"])

        if not can_edit_link_target(request.user, target):
            raise PermissionDenied(_("You are not allowed to link experiments here."))

        connection = _get_connection(request.user)

        # Take the title from eLabFTW, which also confirms that the user can access the experiment
        try:
            experiment = get_experiment(connection, serializer.validated_data["experiment_id"])
        except ElabftwError as e:
            return _elabftw_error_response(e)

        try:
            link = ExperimentLink.objects.create(
                target_type=target_type,
                target_id=target.pk,
                elabftw_url=connection.url,
                experiment_id=experiment["id"],
                title=experiment["title"][:500],
            )
        except (ValidationError, IntegrityError) as e:
            raise serializers.ValidationError({"experiment_id": _("This experiment is already linked here.")}) from e

        return Response(ExperimentLinkSerializer(link).data, status=status.HTTP_201_CREATED)

    def destroy(self, request, *args, **kwargs):
        link = ExperimentLink.objects.filter(pk=kwargs.get("pk")).first()
        target = get_link_target(link.target_type, link.target_id) if link else None

        if not link or not can_view_link_target(request.user, target):
            raise NotFound()

        if not can_edit_link_target(request.user, target):
            raise PermissionDenied(_("You are not allowed to remove experiment links here."))

        link.delete()

        return Response(status=status.HTTP_204_NO_CONTENT)
