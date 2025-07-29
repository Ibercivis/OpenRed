from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.contrib.auth.models import User
from .models import Mission, Campaign, UserGroup
from .serializers import MissionSerializer, CampaignSerializer
from .forms import UserGroupForm
from rest_framework import viewsets

# Create your views here.

class MissionViewSet(viewsets.ModelViewSet):
    queryset = Mission.objects.all()
    serializer_class = MissionSerializer

class CampaignViewSet(viewsets.ModelViewSet):
    queryset = Campaign.objects.all()
    serializer_class = CampaignSerializer

@login_required
def create_user_group(request):
    if request.method == 'POST':
        form = UserGroupForm(request.POST)
        if form.is_valid():
            group = form.save(commit=False)
            group.created_by = request.user
            group.save()
            form.save_m2m()
            messages.success(request, f'Grupo "{group.name}" creado exitosamente.')
            return redirect('list_user_groups')
    else:
        form = UserGroupForm()
    
    return render(request, 'missions/create_user_group.html', {'form': form})

@login_required
def list_user_groups(request):
    groups = UserGroup.objects.all().order_by('-created_at')
    return render(request, 'missions/list_user_groups.html', {'groups': groups})

@login_required
def edit_user_group(request, group_id):
    group = get_object_or_404(UserGroup, id=group_id)
    
    if request.method == 'POST':
        form = UserGroupForm(request.POST, instance=group)
        if form.is_valid():
            form.save()
            messages.success(request, f'Grupo "{group.name}" actualizado exitosamente.')
            return redirect('list_user_groups')
    else:
        form = UserGroupForm(instance=group)
    
    return render(request, 'missions/edit_user_group.html', {'form': form, 'group': group})