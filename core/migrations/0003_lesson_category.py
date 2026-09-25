from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [('core', '0002_lms_portal')]
    operations = [migrations.AddField(model_name='lesson', name='category', field=models.CharField(choices=[('LESSON','Учебный материал'),('NPA_RK','НПА РК'),('INTERNAL','Внутренние документы')], default='LESSON', max_length=12, verbose_name='Раздел курса'))]
