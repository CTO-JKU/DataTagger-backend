from channels.layers import get_channel_layer
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django_userforeignkey.models.fields import UserForeignKey
from django_userforeignkey.request import get_current_user

__all__ = [
    "BaseModel",
    "TimestampMixin",
    "ByUserMixin",
    "LockMixin",
    "SyncedMixin",
    "OrderMixin",
    "AddressMixin",
    "ApprovalQueueMixin",
]


class BaseModel(models.Model):
    @classmethod
    def get_content_type(cls):
        # if we are working on a deferred proxy class, we first need to get
        # the real model class, so we can save a new instance if we need.
        if getattr(cls, "_deferred", False):
            cls = cls.__mro__[1]

        try:
            return ContentType.objects.get_for_model(cls)
        except ContentType.DoesNotExist:
            return None

    class Meta:
        abstract = True

    # def is_viewable(self):
    #     if self.pk and hasattr(self.__class__.objects, "viewable"):
    #         return bool(self.__class__.objects.viewable().filter(pk=self.pk).count())
    #     return True
    #
    # def is_addable(self):
    #     if self.pk and hasattr(self.__class__.objects, "addable"):
    #         return bool(self.__class__.objects.addable().filter(pk=self.pk).count())
    #     return True
    #
    # def is_editable(self):
    #     if self.pk and hasattr(self.__class__.objects, "editable"):
    #         return bool(self.__class__.objects.editable().filter(pk=self.pk).count())
    #     return True
    #
    # def is_deletable(self):
    #     if self.pk and hasattr(self.__class__.objects, "deletable"):
    #         return bool(self.__class__.objects.deletable().filter(pk=self.pk).count())
    #     return True

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        """
        Uses `str_fields` and `str_delimiter` from the meta model to build a string representation.
        Example usage:
        ```python
        class MyModel(BaseModel):
            a = IntegerField()
            b = TextField()
            c = BooleanField()

            class Meta:
                str_fields = ("a", "c",)
        ```
        """
        str_field_names = getattr(self.__class__._meta, "str_fields", None)
        if str_field_names:
            field_values = (getattr(self, name, None) for name in str_field_names)
            data = (str(value) for value in field_values if value)
            delimiter = getattr(self._meta, "str_delimiter", " | ")
            return delimiter.join(data)
        else:
            return str(self.id)


class TimestampMixin(models.Model):
    creation_date = models.DateTimeField(
        verbose_name=_("Creation date"),
        blank=False,
        null=False,
        auto_now_add=True,
    )

    last_modification_date = models.DateTimeField(
        verbose_name=_("Last modification date"),
        blank=False,
        null=False,
        auto_now=True,
    )

    def is_modified(self):
        return self.creation_date != self.last_modification_date

    class Meta:
        abstract = True


class ByUserMixin(models.Model):
    created_by = UserForeignKey(
        verbose_name=_("User who created this element"),
        auto_user_add=True,  # sets the current user when the element is created
        null=True,
        related_name="%(class)s_created",
    )

    last_modified_by = UserForeignKey(
        verbose_name=_("User who last modified this element"),
        auto_user=True,  # sets the current user everytime the element is saved
        null=True,
        related_name="%(class)s_modified",
    )

    class Meta:
        abstract = True


class LockMixin(models.Model):
    locked = models.BooleanField(
        verbose_name=_("Whether this element is locked"),
        default=False,
    )

    locked_by = UserForeignKey(
        verbose_name=_("User who locked this element"),
        blank=True,
        null=True,
        related_name="%(class)s_locked_by",
        on_delete=models.SET_NULL,
    )

    locked_at = models.DateTimeField(
        verbose_name=_("Locked at"),
        blank=True,
        null=True,
        default=None,
    )

    class Meta:
        abstract = True

    def lock_is_expired(self) -> bool:
        from fdm.core.helpers import get_max_lock_time

        if not self.locked_at:
            return True

        max_lock_time = get_max_lock_time()

        if timezone.now() - self.locked_at >= timezone.timedelta(minutes=max_lock_time):
            return True

        return False

    def is_locked_by_myself(self) -> bool:
        return self.locked_by == get_current_user()

    def remove_expired_lock(self, save=True) -> None:
        if self.lock_is_expired():
            self.unlock(save=save)

    def lock(self, save=True, auto_unlock=False) -> None:
        current_user = get_current_user()

        self.locked_by = current_user if current_user and current_user.pk else None
        self.locked_at = timezone.now()
        self.locked = True

        if save:
            self.save(auto_unlock=auto_unlock)

            channel_layer = get_channel_layer()
            if channel_layer and self.locked:
                from fdm.core.helpers import send_websocket_message

                model_name = self.__class__.__name__.lower()
                group_name = "lock_status_changes"
                send_websocket_message(
                    channel_layer,
                    group_name,
                    {
                        "type": "lock_status_changed",
                        "data": {
                            "content_type": model_name,
                            "pk": str(self.pk),
                            "status": True,
                            "user": current_user.email if current_user and current_user.is_authenticated else None,
                        },
                    },
                )

    def unlock(self, save=True) -> None:
        self.locked_by = None
        self.locked_at = None
        self.locked = False

        if save:
            self.save()

            channel_layer = get_channel_layer()
            if channel_layer:
                from fdm.core.helpers import send_websocket_message

                model_name = self.__class__.__name__.lower()
                group_name = "lock_status_changes"
                send_websocket_message(
                    channel_layer,
                    group_name,
                    {
                        "type": "lock_status_changed",
                        "data": {
                            "content_type": model_name,
                            "pk": str(self.pk),
                            "status": False,
                        },
                    },
                )

    def save(self, auto_unlock=True, *args, **kwargs):
        self.remove_expired_lock(False)

        if self.locked and not self.is_locked_by_myself():
            raise PermissionDenied(_("You must not edit an element which has been locked by another user."))

        if auto_unlock:
            self.unlock(save=False)

        super().save(*args, **kwargs)


class ApprovalQueueMixin(models.Model):
    approved = models.BooleanField(
        default=False,
    )

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        from fdm.approval_queue.models import ApprovalQueue

        if (
            not self.approved
            and not ApprovalQueue.objects.filter(
                content_type=ContentType.objects.get_for_model(self),
                object_id=self.id,
            ).exists()
        ):
            ApprovalQueue.objects.create(
                content_object=self,
            )

        super().save(*args, **kwargs)


class SyncedMixin(models.Model):
    original_id = models.BigIntegerField(
        verbose_name=_("Original ID"),
        blank=True,
        null=True,
        editable=False,
        db_index=True,
    )

    last_sync = models.DateTimeField(
        verbose_name=_("Last synchronisation date"),
        blank=True,
        null=True,
        editable=False,
        db_index=True,
    )

    def is_synced(self):
        return self.original_id is not None

    class Meta:
        abstract = True


class OrderMixin(models.Model):
    class Meta:
        abstract = True

    order = models.IntegerField(
        _("Order"),
        blank=True,
        null=True,
    )


class AddressMixin(models.Model):
    class Meta:
        abstract = True

    street = models.CharField(
        verbose_name=_("street"),
        max_length=255,
    )

    street_number = models.CharField(
        verbose_name=_("street number"),
        max_length=255,
    )

    zip = models.CharField(
        verbose_name=_("zip"),
        max_length=15,
    )

    city = models.CharField(
        verbose_name=_("city"),
        max_length=255,
    )

    country = models.CharField(
        verbose_name=_("country"),
        max_length=255,
    )
