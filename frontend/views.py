from django.shortcuts import render
from django.conf import settings

# Create your views here.

def index(request):
    print(settings.MAPBOX_ACCESS_TOKEN)
    context = {
        'mapbox_token': settings.MAPBOX_ACCESS_TOKEN
    }
    return render(request, 'frontend/index.html', context)
