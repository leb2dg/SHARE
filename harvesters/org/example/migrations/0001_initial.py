# -*- coding: utf-8 -*-
# Generated by Django 1.9.6 on 2016-06-09 21:09
from __future__ import unicode_literals

from django.apps import apps
from django.db import migrations

from share import core


class Migration(migrations.Migration):

    dependencies = [
        ('share', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(
            core.CreateHarvesterUser(apps.get_app_config('example')),
            core.RemoveHarvesterUser(apps.get_app_config('example')),
        ),
    ]
