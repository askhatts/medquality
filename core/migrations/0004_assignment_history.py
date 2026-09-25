from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [('core', '0003_lesson_category')]

    operations = [
        migrations.AlterUniqueTogether(
            name='assignment',
            unique_together=set(),
        ),
        migrations.AlterModelOptions(
            name='assignment',
            options={'ordering': ['-assigned_at', '-id']},
        ),
    ]
