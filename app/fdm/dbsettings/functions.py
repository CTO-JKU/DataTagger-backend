from django.utils.functional import LazyObject, empty

from .models import Setting

__all__ = [
    "DBSettings",
    "LazyDBSettings",
    "get_dbsettings_value",
    "set_dbsettings_value",
    "dbsettings",
]


class DBSettings:
    def __init__(self):
        for setting in Setting.objects.all():
            if setting.key.isupper():
                setattr(self, setting.key, setting.value)

    def __setattr__(self, name, value):
        setting, _ = Setting.objects.get_or_create(key=name)
        setting.value = value
        setting.save()

        self.__dict__[name] = value

    def __delattr__(self, name):
        setting = Setting.objects.get(key=name)
        setting.delete()

        self.__dict__.pop(name, None)


class LazyDBSettings(LazyObject):
    def _setup(self):
        self._wrapped = DBSettings()

    def __getattr__(self, name):
        if self._wrapped is empty:
            self._setup()

        val = getattr(self._wrapped, name)

        self.__dict__[name] = val
        return val

    def __setattr__(self, name, value):
        if name == "_wrapped":
            self.__dict__.clear()
        else:
            self.__dict__.pop(name, None)

        super().__setattr__(name, value)

    def __delattr__(self, name):
        super().__delattr__(name)
        self.__dict__.pop(name, None)


def get_dbsettings_value(key: str, default: any = None) -> str:
    try:
        return Setting.objects.get(key=key).value
    except Exception:
        if default is None:
            raise KeyError("No such setting: %s" % key)
        else:
            return default


def set_dbsettings_value(key: str, value: str) -> None:
    instance = Setting.objects.get_or_create(key=key)[0]  # pylint: disable=E1101
    instance.value = value
    instance.save()


dbsettings = LazyDBSettings()
