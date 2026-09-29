from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('core', '0012_assignment_reassigned_status')]

    operations = [
        migrations.AddField(
            model_name='test',
            name='time_limit_minutes',
            field=models.PositiveSmallIntegerField(default=0, verbose_name='Лимит времени, минут'),
        ),
    ]
