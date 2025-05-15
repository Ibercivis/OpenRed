from django.shortcuts import render, redirect
from django.conf import settings
from devices.forms import DeviceForm 
from missions.models import Campaign
from django.utils import timezone
# Create your views here.

def index(request):
    campaigns = Campaign.objects.all()
    context = {
        'mapbox_token': settings.MAPBOX_ACCESS_TOKEN,
        'campaigns': campaigns,
    }
    return render(request, 'frontend/index.html', context)

def add_device(request):
    if request.method == 'POST':
        form = DeviceForm(request.POST)
        if form.is_valid():
            device = form.save(commit=False)
            device.owner = request.user  # Asignar usuario autenticado
            device.created_at = timezone.now()  # Si tienes un campo así
            device.save()
            return redirect('index')  # O a donde quieras redirigir
    else:
        form = DeviceForm()

    return render(request, 'frontend/add_device.html', {'form': form})