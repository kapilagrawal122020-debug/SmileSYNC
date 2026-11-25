from django.db import migrations, models
from django.utils.text import slugify


def give_every_clinic_a_web_address(apps, schema_editor):
    Clinic = apps.get_model("clinic", "Clinic")
    taken = set()
    for clinic in Clinic.objects.order_by("id"):
        base = slugify(f"{clinic.name} {clinic.city}".strip())[:150] or "clinic"
        slug, n = base, 1
        while slug in taken:
            n += 1
            slug = f"{base}-{n}"
        taken.add(slug)
        clinic.slug = slug
        clinic.save(update_fields=["slug"])


class Migration(migrations.Migration):

    dependencies = [
        ("clinic", "0003_alter_clinic_options_clinic_admin_notes_clinic_email_and_more"),
    ]

    operations = [
        migrations.AddField(model_name="clinic", name="about", field=models.TextField(blank=True)),
        migrations.AddField(model_name="clinic", name="timings", field=models.CharField(blank=True, max_length=200)),
        # added without the unique rule first, filled in for the clinics that exist, then made unique
        migrations.AddField(model_name="clinic", name="slug", field=models.SlugField(blank=True, default="", max_length=170)),
        migrations.RunPython(give_every_clinic_a_web_address, migrations.RunPython.noop),
        migrations.AlterField(model_name="clinic", name="slug", field=models.SlugField(blank=True, max_length=170, unique=True)),
    ]
