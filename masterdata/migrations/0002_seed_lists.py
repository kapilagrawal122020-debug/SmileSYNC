from django.db import migrations

from masterdata import services


def seed(apps, schema_editor):
    """Load the 67 starting lists. Re-running adds what is missing and never overwrites an edit."""
    services.load_seed(apps.get_model("masterdata", "ListDefinition"), apps.get_model("masterdata", "ListValue"))


class Migration(migrations.Migration):
    dependencies = [("masterdata", "0001_initial")]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
