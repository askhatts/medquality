from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('core', '0010_neutral_direction_descriptions')]
    operations = [
        migrations.AddField(
            model_name='assignment',
            name='due_time',
            field=models.TimeField(blank=True, null=True, verbose_name='Время окончания'),
        ),
    ]
