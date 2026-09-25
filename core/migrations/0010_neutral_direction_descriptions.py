from django.db import migrations


OLD_TEXT = 'Обучение и доказательства непрерывного повышения качества медицинских услуг.'
NEW_TEXT = 'Обучение и практические материалы для постоянного улучшения качества медицинских услуг.'


def forwards(apps, schema_editor):
    Direction = apps.get_model('core', 'QualityDirection')
    Direction.objects.filter(description=OLD_TEXT).update(description=NEW_TEXT)


def backwards(apps, schema_editor):
    Direction = apps.get_model('core', 'QualityDirection')
    Direction.objects.filter(description=NEW_TEXT).update(description=OLD_TEXT)


class Migration(migrations.Migration):
    dependencies = [('core', '0009_link_requirements_to_courses')]
    operations = [migrations.RunPython(forwards, backwards)]
