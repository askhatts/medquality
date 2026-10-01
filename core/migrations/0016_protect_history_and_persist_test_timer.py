import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('core', '0015_assignment_cancellation'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='assignment',
            name='current_attempt_started_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name='assignment',
            name='course',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='assignments', to='core.course'),
        ),
        migrations.AlterField(
            model_name='assignment',
            name='user',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='assignments', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AlterField(
            model_name='course',
            name='direction',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='courses', to='core.qualitydirection', verbose_name='Направление качества'),
        ),
        migrations.AlterField(
            model_name='testattempt',
            name='assignment',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='attempts', to='core.assignment'),
        ),
    ]
