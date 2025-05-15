from django.contrib import admin

# Register your models here.
from .models import Campaign, Mission

# Register your models here.
admin.site.register(Mission)
admin.site.register(Campaign)