from allauth.account.adapter import DefaultAccountAdapter
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from django.contrib.auth import get_user_model
from django.conf import settings
from django.utils.http import urlencode
import uuid
import re

User = get_user_model()


class EmailUsernameAdapter(DefaultAccountAdapter):
    """
    Adaptador personalizado para generar automáticamente el username
    basado en el email y evitar conflictos.
    """
    
    def generate_unique_username(self, txts, regex=None):
        """
        Genera un username único basado en el email.
        """
        # Usar el email como base para el username
        if txts and len(txts) > 0:
            base_username = txts[0].split('@')[0]  # Parte antes del @
        else:
            base_username = 'user'
        
        # Limpiar caracteres especiales
        base_username = re.sub(r'[^\w]', '', base_username).lower()
        
        # Si el username está vacío, usar 'user'
        if not base_username:
            base_username = 'user'
        
        # Verificar si ya existe
        username = base_username
        counter = 1
        
        while User.objects.filter(username=username).exists():
            username = f"{base_username}_{counter}"
            counter += 1
            
            # Evitar bucles infinitos
            if counter > 1000:
                username = f"{base_username}_{uuid.uuid4().hex[:8]}"
                break
        
        return username
    
    def save_user(self, request, user, form, commit=True):
        """
        Sobrescribir para generar username automáticamente.
        """
        # Manejar tanto formularios de Django como serializadores
        if hasattr(form, 'cleaned_data'):
            # Formulario tradicional de Django
            data = form.cleaned_data
        elif hasattr(form, 'get_cleaned_data'):
            # Serializador que implementa get_cleaned_data (nuestro caso)
            data = form.get_cleaned_data()
        elif hasattr(form, 'validated_data'):
            # Serializador DRF directo
            data = form.validated_data
        else:
            # Fallback - intentar obtener datos como diccionario
            data = form if isinstance(form, dict) else {}
            
        print(f"Datos del formulario en save_user: {data}")  # Debug log
        
        # Establecer campos básicos
        user.email = data.get('email', user.email)
        
        print(f"Usuario guardado con: email={user.email}")  # Debug log
        
        # Generar username automáticamente si no se proporciona
        if not user.username:
            user.username = self.generate_unique_username([user.email])
        
        # Solo establecer contraseña si está en los datos y el usuario no la tiene ya
        if 'password1' in data and not user.password:
            user.set_password(data['password1'])
        
        if commit:
            user.save()
        
        return user
    
    def get_email_confirmation_url(self, request, emailconfirmation):
        """
        Generar URL que apunte al frontend para verificar email.
        Usa FRONTEND_URL de settings (configurado via .env)
        
        Development: http://localhost:3000/verify-email?key=...
        Production: https://map.open-red.es/verify-email?key=...
        """
        return f"{settings.FRONTEND_URL}/verify-email?key={emailconfirmation.key}"
    
    def get_email_verification_redirect_url(self, email_address):
        """
        Redirección después de confirmar email exitosamente (nuevo método).
        Usa FRONTEND_URL de settings (configurado via .env)
        
        Development: http://localhost:3000/email-confirmed?status=success
        Production: https://map.open-red.es/email-confirmed?status=success
        """
        # pylint: disable=unused-argument
        return getattr(settings, 'ACCOUNT_EMAIL_CONFIRMATION_AUTHENTICATED_REDIRECT_URL', 
                      f"{settings.FRONTEND_URL}/email-confirmed?status=success")

    def get_email_confirmation_redirect_url(self, request):
        """
        URL de redirección después de confirmar email (método legacy).
        Usa FRONTEND_URL de settings (configurado via .env)
        
        Development: http://localhost:3000/email-confirmed?status=success&message=...
        Production: https://map.open-red.es/email-confirmed?status=success&message=...
        """
        # pylint: disable=unused-argument
        # Construir URL con parámetros
        base_url = getattr(settings, 'ACCOUNT_EMAIL_CONFIRMATION_AUTHENTICATED_REDIRECT_URL', 
                          f"{settings.FRONTEND_URL}/email-confirmed")
        
        # Agregar parámetros de éxito
        params = {
            'status': 'success',
            'message': 'Tu cuenta ha sido verificada exitosamente.'
        }
        
        url_with_params = f"{base_url}?{urlencode(params)}"
        return url_with_params


class AutoConnectSocialAccountAdapter(DefaultSocialAccountAdapter):
    """
    Adapter personalizado que vincula automáticamente cuentas sociales
    con cuentas existentes si el email coincide.
    """
    
    def pre_social_login(self, request, sociallogin):
        """
        Invocado justo después de que un usuario se autentica con éxito 
        mediante un proveedor social, pero antes de que la cuenta se conecte.
        
        Si ya existe una cuenta con el mismo email verificado, la vincula automáticamente.
        """
        # Si el usuario ya está autenticado, no hacer nada
        if sociallogin.is_existing:
            return
        
        # Si el social login no tiene email, no podemos vincular
        if not sociallogin.email_addresses:
            return
        
        # Obtener el email del social login
        email = sociallogin.email_addresses[0].email
        
        try:
            # Buscar usuario existente con ese email
            user = User.objects.get(email=email)
            
            # Vincular la cuenta social con el usuario existente
            sociallogin.connect(request, user)
            
        except User.DoesNotExist:
            # No existe usuario, se creará uno nuevo (comportamiento por defecto)
            pass

