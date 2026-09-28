from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('core', '0011_assignment_due_time')]
    operations = [
        migrations.AlterField(
            model_name='assignment',
            name='status',
            field=models.CharField(
                choices=[
                    ('ASSIGNED', 'Назначено'),
                    ('IN_PROGRESS', 'В процессе'),
                    ('COMPLETED', 'Пройдено'),
                    ('OVERDUE', 'Просрочено'),
                    ('FAILED', 'Не пройдено'),
                    ('REASSIGNED', 'Переназначено'),
                ],
                default='ASSIGNED', max_length=12, verbose_name='Статус',
            ),
        ),
    ]
