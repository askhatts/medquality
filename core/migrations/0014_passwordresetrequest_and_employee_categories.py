from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def normalize_employee_categories(apps, schema_editor):
    EmployeeType = apps.get_model('core', 'EmployeeType')
    Profile = apps.get_model('core', 'Profile')

    EmployeeType.objects.get_or_create(name='Врач')
    nurse, _ = EmployeeType.objects.get_or_create(name='Медсестра')
    for legacy in EmployeeType.objects.filter(name='Средний медицинский персонал'):
        Profile.objects.filter(employee_type=legacy).update(employee_type=nurse)
        legacy.delete()


class Migration(migrations.Migration):
    dependencies = [
        ('core', '0013_test_time_limit_minutes'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='PasswordResetRequest',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('status', models.CharField(choices=[('PENDING', 'Ожидает'), ('COMPLETED', 'Выполнен'), ('DISMISSED', 'Закрыт')], default='PENDING', max_length=12, verbose_name='Статус')),
                ('requested_at', models.DateTimeField(auto_now_add=True, verbose_name='Запрошено')),
                ('resolved_at', models.DateTimeField(blank=True, null=True, verbose_name='Обработано')),
                ('resolved_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='resolved_password_reset_requests', to=settings.AUTH_USER_MODEL)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='password_reset_requests', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-requested_at'],
            },
        ),
        migrations.AddConstraint(
            model_name='passwordresetrequest',
            constraint=models.UniqueConstraint(condition=models.Q(status='PENDING'), fields=('user',), name='unique_pending_password_reset_request'),
        ),
        migrations.RunPython(normalize_employee_categories, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='employeetype',
            name='name',
            field=models.CharField(max_length=80, unique=True, verbose_name='Категория сотрудника'),
        ),
        migrations.AlterField(
            model_name='profile',
            name='employee_type',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to='core.employeetype', verbose_name='Категория сотрудника'),
        ),
    ]
