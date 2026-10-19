from django.db import migrations, models
from django.utils import timezone


class Migration(migrations.Migration):
    dependencies = [
        ('core', '0017_passwordresetrequest_contact'),
    ]

    operations = [
        migrations.AlterField(
            model_name='internaldocument',
            name='file',
            field=models.FileField(blank=True, upload_to='internal_documents/', verbose_name='PDF'),
        ),
        migrations.AddField(
            model_name='internaldocument',
            name='external_url',
            field=models.URLField(blank=True, max_length=500, verbose_name='Ссылка Google Drive'),
        ),
        migrations.AddField(
            model_name='internaldocument',
            name='updated_at',
            field=models.DateTimeField(auto_now=True, default=timezone.now),
            preserve_default=False,
        ),
    ]
