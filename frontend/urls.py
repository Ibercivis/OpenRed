from django.urls import path
from . import views

urlpatterns = [
    path('', views.index, name='index'),
    path('add_device/', views.add_device, name='add_device'),
    path('login/', views.login_view, name='login'),
    path('register/', views.register_view, name='register'),
]