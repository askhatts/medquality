from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('core', '0016_protect_history_and_persist_test_timer'),
    ]

    operations = [
        migrations.AddField(
            model_name='passwordresetrequest',
            name='contact_method',
            field=models.CharField(
                choices=[('WHATSAPP', 'WhatsApp'), ('EMAIL', 'Электронная почта')],
                default='WHATSAPP', max_length=12, verbose_name='Способ связи',
            ),
        ),
        migrations.AddField(
            model_name='passwordresetrequest',
            name='contact_value',
            field=models.CharField(blank=True, max_length=254, verbose_name='Контакт для временного пароля'),
        ),
    ]
