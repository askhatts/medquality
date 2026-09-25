from django.db import migrations


def link(apps, schema_editor):
    Requirement = apps.get_model('core', 'QualityRequirement')
    for requirement in Requirement.objects.all():
        requirement.courses.add(*requirement.direction.courses.values_list('id', flat=True))


class Migration(migrations.Migration):
    dependencies = [('core', '0008_internaldocument_document_type_and_more')]
    operations = [migrations.RunPython(link, migrations.RunPython.noop)]
