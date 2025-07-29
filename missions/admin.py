from django.contrib import admin
from .models import Mission, Campaign, UserGroup

@admin.register(UserGroup)
class UserGroupAdmin(admin.ModelAdmin):
    list_display = ['name', 'get_user_count', 'created_by', 'created_at']
    list_filter = ['created_at', 'created_by']
    search_fields = ['name', 'description']
    filter_horizontal = ['users']

@admin.register(Campaign)
class CampaignAdmin(admin.ModelAdmin):
    list_display = ['name', 'mission', 'get_total_participants_count', 'start_date', 'end_date']
    list_filter = ['mission', 'start_date', 'end_date']
    search_fields = ['name', 'description']
    filter_horizontal = ['participants', 'user_groups', 'devices']

@admin.register(Mission)
class MissionAdmin(admin.ModelAdmin):
    list_display = ['name', 'start_date', 'end_date', 'created_at']
    list_filter = ['start_date', 'end_date', 'created_at']
    search_fields = ['name', 'description']