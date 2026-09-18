from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('measures', '0021_add_range_filter_indexes'),
    ]

    operations = [
        migrations.AddField(
            model_name='track',
            name='track_type',
            field=models.CharField(
                choices=[('radiation', 'Radiation'), ('light', 'Light Pollution')],
                default='radiation',
                help_text='Tipo de medición del track (radiation/light)',
                max_length=20,
                verbose_name='Tipo de Track',
            ),
        ),
    ]
