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
    Custom adapter that generates the username from the email address and
    avoids collisions.
    """
    
    def generate_unique_username(self, txts, regex=None):
        """
        Generates a unique username from the email address.
        """
        # The email address is the base for the username
        if txts and len(txts) > 0:
            base_username = txts[0].split('@')[0]  # The part before the @
        else:
            base_username = 'user'
        
        # Strip the special characters
        base_username = re.sub(r'[^\w]', '', base_username).lower()
        
        # An empty username becomes 'user'
        if not base_username:
            base_username = 'user'
        
        # Check whether it is taken already
        username = base_username
        counter = 1
        
        while User.objects.filter(username=username).exists():
            username = f"{base_username}_{counter}"
            counter += 1
            
            # Do not loop forever
            if counter > 1000:
                username = f"{base_username}_{uuid.uuid4().hex[:8]}"
                break
        
        return username
    
    def save_user(self, request, user, form, commit=True):
        """
        Overridden so the username is generated.
        """
        # Both Django forms and serializers get here
        if hasattr(form, 'cleaned_data'):
            # An ordinary Django form
            data = form.cleaned_data
        elif hasattr(form, 'get_cleaned_data'):
            # A serializer implementing get_cleaned_data (this project's case)
            data = form.get_cleaned_data()
        elif hasattr(form, 'validated_data'):
            # A plain DRF serializer
            data = form.validated_data
        else:
            # Fallback: read the data as a dictionary
            data = form if isinstance(form, dict) else {}
            
        print(f"Form data in save_user: {data}")  # Debug log
        
        # The basic fields
        user.email = data.get('email', user.email)
        
        print(f"User saved with: email={user.email}")  # Debug log
        
        # Generate the username when none was given
        if not user.username:
            user.username = self.generate_unique_username([user.email])
        
        # Set a password only when the data carries one and the user has none
        if 'password1' in data and not user.password:
            user.set_password(data['password1'])
        
        if commit:
            user.save()
        
        return user
    
    def get_email_confirmation_url(self, request, emailconfirmation):
        """
        The address the confirmation link points at: the front end's own page.
        Taken from FRONTEND_URL in settings (set through .env).
        
        Development: http://localhost:3000/verify-email?key=...
        Production: https://map.open-red.es/verify-email?key=...
        """
        return f"{settings.FRONTEND_URL}/verify-email?key={emailconfirmation.key}"
    
    def get_email_verification_redirect_url(self, email_address):
        """
        Where a confirmed address lands (the current method).
        Taken from FRONTEND_URL in settings (set through .env).
        
        Development: http://localhost:3000/email-confirmed?status=success
        Production: https://map.open-red.es/email-confirmed?status=success
        """
        # pylint: disable=unused-argument
        return getattr(settings, 'ACCOUNT_EMAIL_CONFIRMATION_AUTHENTICATED_REDIRECT_URL', 
                      f"{settings.FRONTEND_URL}/email-confirmed?status=success")

    def get_email_confirmation_redirect_url(self, request):
        """
        Where a confirmed address lands (the method allauth has deprecated).
        Taken from FRONTEND_URL in settings (set through .env).
        
        Development: http://localhost:3000/email-confirmed?status=success&message=...
        Production: https://map.open-red.es/email-confirmed?status=success&message=...
        """
        # pylint: disable=unused-argument
        # Build the address with its parameters
        base_url = getattr(settings, 'ACCOUNT_EMAIL_CONFIRMATION_AUTHENTICATED_REDIRECT_URL', 
                          f"{settings.FRONTEND_URL}/email-confirmed")
        
        # Say it worked
        params = {
            'status': 'success',
            'message': 'Tu cuenta ha sido verificada exitosamente.'
        }
        
        url_with_params = f"{base_url}?{urlencode(params)}"
        return url_with_params


class AutoConnectSocialAccountAdapter(DefaultSocialAccountAdapter):
    """
    Custom adapter that connects a social account to an account that already
    exists here when the email address matches.
    """
    
    def pre_social_login(self, request, sociallogin):
        """
        Called just after somebody authenticates with a social provider and
        before the account is connected.

        An account that already exists with the same verified address is
        connected to it.
        """
        # Nothing to do for a social account already connected
        if sociallogin.is_existing:
            return
        
        # Without an address there is nothing to match on
        if not sociallogin.email_addresses:
            return
        
        # The address the social login brought
        email = sociallogin.email_addresses[0].email
        
        try:
            # Look for an account already holding it
            user = User.objects.get(email=email)
            
            # Connect the social account to it
            sociallogin.connect(request, user)
            
        except User.DoesNotExist:
            # No account holds it: a new one is created, as allauth does by default
            pass

