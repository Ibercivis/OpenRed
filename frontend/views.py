from django.shortcuts import render, redirect
from django.conf import settings
from devices.forms import DeviceForm 
from missions.models import Campaign
from django.utils import timezone


def index(request):
    """
    Home page view displaying campaigns and map.
    """
    campaigns = Campaign.objects.all()
    context = {
        'mapbox_token': settings.MAPBOX_ACCESS_TOKEN,
        'campaigns': campaigns,
    }
    return render(request, 'frontend/index.html', context)


def add_device(request):
    """
    View for adding a new device to the authenticated user's account.
    """
    if request.method == 'POST':
        form = DeviceForm(request.POST)
        if form.is_valid():
            device = form.save(commit=False)
            device.owner = request.user
            device.created_at = timezone.now()
            device.save()
            return redirect('index')
    else:
        form = DeviceForm()

    return render(request, 'frontend/add_device.html', {'form': form})


def login_view(request):
    """
    Login page view.
    Authentication is handled client-side via dj-rest-auth API.
    """
    if request.user.is_authenticated:
        return redirect('index')
    return render(request, 'frontend/login.html')


def register_view(request):
    """
    Registration page view.
    Registration is handled client-side via dj-rest-auth API.
    """
    if request.user.is_authenticated:
        return redirect('index')
    return render(request, 'frontend/register.html')