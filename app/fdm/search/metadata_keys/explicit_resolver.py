from uuid import UUID

from rest_framework.exceptions import NotFound

from fdm.metadata.models import MetadataTemplate

TEMPLATE_SCOPE = "template"

ScopePair = tuple[MetadataTemplate, str, object]


class ExplicitTemplateResolver:
    # Viewability is enforced by build_viewable_templates_queryset, which reads the current
    # user from django-userforeignkey's thread-local request (set by middleware on the
    # request path, or by set_request_for_user() in tests). Not-viewable and not-existing
    # both surface as 404 here, deliberately, so we don't leak template existence to users
    # who can't see them.
    def resolve(self, template_ids: list[UUID]) -> list[ScopePair]:
        from fdm.search.services import build_viewable_templates_queryset

        if not template_ids:
            return []

        normalised = [UUID(str(tid)) for tid in template_ids]
        viewable_by_pk = {
            container.pk: container for container in build_viewable_templates_queryset().filter(pk__in=normalised)
        }
        if any(tid not in viewable_by_pk for tid in normalised):
            raise NotFound("Template not found.")

        latest_pks = [
            container.search_latest_template_pk
            for container in viewable_by_pk.values()
            if container.search_latest_template_pk is not None
        ]
        latest_by_pk = {template.pk: template for template in MetadataTemplate.objects.filter(pk__in=latest_pks)}

        pairs: list[ScopePair] = []
        for tid in normalised:
            container = viewable_by_pk[tid]
            latest = latest_by_pk.get(container.search_latest_template_pk)
            if latest is None:
                continue
            pairs.append((latest, TEMPLATE_SCOPE, container.pk))
        return pairs
