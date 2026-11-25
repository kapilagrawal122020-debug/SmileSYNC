from django.db import migrations


def promote_clinic_owners(apps, schema_editor):
    """Until now the doctor who signed a clinic up was its owner. Give that person the clinic admin role,
    so existing clinics keep someone who can manage the team. Other doctors stay doctors."""
    User = apps.get_model("accounts", "User")
    Clinic = apps.get_model("clinic", "Clinic")
    for clinic in Clinic.objects.all():
        staff = User.objects.filter(clinic=clinic)
        if staff.filter(role="clinic_admin").exists():
            continue
        owner = staff.filter(role="doctor", is_active=True).order_by("id").first() or staff.filter(role="doctor").order_by("id").first()
        if owner:
            owner.role = "clinic_admin"
            owner.save(update_fields=["role"])


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0004_clinic_admin_role_and_auth_token"),
        ("clinic", "0004_marketplace_profile"),
    ]

    operations = [
        migrations.RunPython(promote_clinic_owners, migrations.RunPython.noop),
    ]
