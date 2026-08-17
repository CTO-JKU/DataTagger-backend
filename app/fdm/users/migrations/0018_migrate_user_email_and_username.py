from django.db import migrations
from django.db.models.functions import Lower


def forwards_func(apps, schema_editor):
    User = apps.get_model("users", "User")
    User.objects.update(
        email=Lower("email"),
        username=Lower("username"),
    )


class Migration(migrations.Migration):
    dependencies = [
        ("users", "0017_user_settings"),
    ]

    operations = [
        migrations.RunPython(
            code=forwards_func,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
