import pytest
from django.conf import settings

from fdm.core.helpers import get_max_lock_time
from fdm.dbsettings.models import Setting


@pytest.mark.django_db
class TestCoreHelper:
    def test_get_max_lock_time(self):
        """
        Ensure we get the correct max lock time.
        """
        time = get_max_lock_time()
        assert time == settings.MAX_LOCK_TIME

        setting = Setting.objects.get(key="MAX_LOCK_TIME")
        setting.value = 10
        setting.save()

        time = get_max_lock_time()
        assert time == 10

        setting.value = 60
        setting.save()

        time = get_max_lock_time()
        assert time == 60

        setting.value = "a"
        setting.save()

        time = get_max_lock_time()
        assert time == settings.MAX_LOCK_TIME

        setting.value = ""
        setting.save()

        time = get_max_lock_time()
        assert time == settings.MAX_LOCK_TIME

        setting.value = 0
        setting.save()

        time = get_max_lock_time()
        assert time == 0
