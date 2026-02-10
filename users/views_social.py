"""
Social authentication views for Google OAuth.
"""
from dj_rest_auth.registration.views import SocialLoginView
from allauth.socialaccount.providers.google.views import GoogleOAuth2Adapter
from allauth.socialaccount.providers.oauth2.client import OAuth2Client
from django.conf import settings


class GoogleLogin(SocialLoginView):
    """
    Login o registro usando Google OAuth para SPAs (React/Vue/móvil).
    
    Flujo recomendado:
    1. Frontend obtiene access_token de Google usando Google Sign-In SDK
    2. Frontend envía el token a este endpoint
    3. Backend valida y devuelve token de autenticación de Django
    
    Uso:
    POST /dj-rest-auth/google/
    {
        "access_token": "ya29.a0AfH6SMB..."
    }
    
    Respuesta exitosa:
    {
        "key": "token_de_autenticacion_django",
        "user": {
            "email": "usuario@gmail.com",
            "username": "usuario_gmail_com",
            ...
        }
    }
    
    Nota: El callback_url apunta al frontend para SPAs.
    Si necesitas el flujo web tradicional, usa: GET /accounts/google/login/
    """
    adapter_class = GoogleOAuth2Adapter
    # Para SPAs, el callback apunta al frontend
    # Google redirigirá aquí después de la autenticación web (si se usa)
    callback_url = settings.FRONTEND_URL
    client_class = OAuth2Client
