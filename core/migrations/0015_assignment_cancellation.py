from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('core', '0014_passwordresetrequest_and_employee_categories'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='assignment',
            name='cancelled_at',
            field=models.DateTimeField(blank=True, null=True, verbose_name='Отменено'),
        ),
        migrations.AddField(
            model_name='assignment',
            name='cancelled_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='cancelled_assignments', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AlterField(
            model_name='assignment',
            name='status',
            field=models.CharField(choices=[('ASSIGNED', 'Назначено'), ('IN_PROGRESS', 'В процессе'), ('COMPLETED', 'Пройдено'), ('OVERDUE', 'Просрочено'), ('FAILED', 'Не пройдено'), ('REASSIGNED', 'Переназначено'), ('CANCELLED', 'Отменено')], default='ASSIGNED', max_length=12, verbose_name='Статус'),
        ),
    ]
