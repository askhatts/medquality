from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('core', '0019_anonymous_wellbeing_survey')]

    operations = [
        migrations.AddField(
            model_name='assignment', name='batch_key',
            field=models.UUIDField('Группа назначения', null=True, blank=True, db_index=True, editable=False),
        ),
    ]
