# Generated by Django 4.2.17 on 2025-07-28 11:18

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('missions', '0001_initial'),
    ]

    operations = [
        migrations.AlterField(
            model_name='campaign',
            name='participants',
            field=models.ManyToManyField(blank=True, related_name='campaigns', to=settings.AUTH_USER_MODEL),
        ),
        migrations.CreateModel(
            name='UserGroup',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=200, unique=True)),
                ('description', models.TextField(blank=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('created_by', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='created_groups', to=settings.AUTH_USER_MODEL)),
                ('users', models.ManyToManyField(related_name='user_groups', to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.AddField(
            model_name='campaign',
            name='user_groups',
            field=models.ManyToManyField(blank=True, related_name='campaigns', to='missions.usergroup'),
        ),
    ]
