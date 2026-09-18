"""
Custom error views for OpenRed API.
"""
from django.shortcuts import render


def custom_404(request, exception=None):
    """
    Custom 404 error page.
    """
    return render(request, '404.html', status=404)


def custom_500(request):
    """
    Custom 500 error page.
    """
    return render(request, '500.html', status=500)
