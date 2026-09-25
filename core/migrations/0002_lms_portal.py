from django.db import migrations, models
import django.db.models.deletion

class Migration(migrations.Migration):
    dependencies = [('core', '0001_initial')]
    operations = [
        migrations.RenameModel('Standard', 'QualityDirection'),
        migrations.RenameField('Course', 'standard', 'direction'),
        migrations.RenameModel('Material', 'Lesson'),
        migrations.AddField(model_name='qualitydirection', name='active', field=models.BooleanField(default=True, verbose_name='Активно')),
        migrations.AddField(model_name='employeetype', name='name', field=models.CharField(max_length=80, unique=True, verbose_name='Тип сотрудника')) if False else migrations.CreateModel(name='EmployeeType', fields=[('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')), ('name', models.CharField(max_length=80, unique=True, verbose_name='Тип сотрудника'))]),
        migrations.AddField(model_name='profile', name='employee_type', field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to='core.employeetype', verbose_name='Тип сотрудника')),
        migrations.AddField(model_name='profile', name='employee_number', field=models.CharField(blank=True, max_length=60, verbose_name='Табельный номер')),
        migrations.AddField(model_name='profile', name='phone', field=models.CharField(blank=True, max_length=40, verbose_name='Телефон')),
        migrations.AddField(model_name='lesson', name='order', field=models.PositiveSmallIntegerField(default=1, verbose_name='Порядок')),
        migrations.AddField(model_name='assignment', name='passing_score', field=models.PositiveSmallIntegerField(default=80, verbose_name='Порог прохождения, %')),
        migrations.AddField(model_name='assignment', name='attempts_allowed', field=models.PositiveSmallIntegerField(default=3, verbose_name='Количество попыток')),
        migrations.AddField(model_name='assignment', name='completed_at', field=models.DateTimeField(blank=True, null=True, verbose_name='Успешно пройдено')),
        migrations.AddField(model_name='testattempt', name='number', field=models.PositiveSmallIntegerField(default=1, verbose_name='Номер попытки')),
        migrations.AddField(model_name='testattempt', name='correct_answers', field=models.PositiveSmallIntegerField(default=0, verbose_name='Верных ответов')),
        migrations.AlterField(model_name='course', name='direction', field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='courses', to='core.qualitydirection', verbose_name='Направление качества')),
        migrations.AlterUniqueTogether(name='testattempt', unique_together={('assignment', 'number')}),
    ]
