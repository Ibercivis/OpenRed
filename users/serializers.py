from django.contrib.auth.models import User
from rest_framework import serializers
from dj_rest_auth.serializers import PasswordResetSerializer as DefaultPasswordResetSerializer
from django.contrib.auth.forms import PasswordResetForm
from django.conf import settings
from urllib.parse import urlparse


class UserSerializer(serializers.ModelSerializer):
    # The identifier of the Ibercivis account this user signs in with, or null.
    # The profile screen shows it, and it is the only thing stored here about
    # that account (users/ibercivis_account.py).
    ibercivis_account_id = serializers.SerializerMethodField()

    def get_ibercivis_account_id(self, user):
        account = user.socialaccount_set.filter(
            provider=settings.IBERCIVIS_ACCOUNT_PROVIDER,
        ).first()
        return account.uid if account else None

    class Meta:
        model = User
        fields = ['id', 'username', 'email', 'date_joined', 'is_active', 'ibercivis_account_id']
        read_only_fields = ['id', 'username', 'date_joined', 'is_active', 'ibercivis_account_id']


# Serializador personalizado para registro
class CustomRegisterSerializer(serializers.Serializer):
    """
    A simplified registration serializer: an email address and a password.
    """
    email = serializers.EmailField()
    password1 = serializers.CharField(write_only=True, min_length=8)
    password2 = serializers.CharField(write_only=True, min_length=8)

    def validate(self, attrs):
        """
        Checks that the two passwords match.
        """
        if attrs['password1'] != attrs['password2']:
            raise serializers.ValidationError("Las contraseñas no coinciden.")
        return attrs

    def validate_email(self, value):
        """
        Checks that no account holds the address already.
        """
        user = User.objects.filter(email=value).first()
        if user:
            from allauth.socialaccount.models import SocialAccount
            if SocialAccount.objects.filter(user=user, provider='google').exists():
                raise serializers.ValidationError(
                    "Esta cuenta fue creada con Google. Por favor, inicia sesión con Google."
                )
            raise serializers.ValidationError("Un usuario con este email ya existe.")
        return value

    def create(self, validated_data):
        """
        Crear el usuario con los datos validados.
        """
        # Crear el usuario
        user = User.objects.create_user(
            username=validated_data['email'].split('@')[0],  # Username temporal
            email=validated_data['email'],
            password=validated_data['password1']
        )
        return user

    def update(self, instance, validated_data):
        """
        Required by the base serializer; registration never calls it.
        """
        raise NotImplementedError("Update not supported for registration")

    def get_cleaned_data(self):
        """
        What allauth's adapter expects, to read the cleaned data from.
        """
        return {
            'email': self.validated_data['email'],
            'password1': self.validated_data['password1'],
        }

    def save(self, request=None, **kwargs):
        """
        A save() that works with both allauth and dj-rest-auth.
        """
        # request may arrive positionally, which is how dj-rest-auth passes it
        if request is None:
            request = kwargs.get('request')
            
        if not request:
            # Si no hay request, usar create normal
            return self.create(self.validated_data)
            
        from allauth.account.adapter import get_adapter
        from allauth.account.utils import setup_user_email
        
        adapter = get_adapter()
        user = adapter.new_user(request)
        
        # Establecer los datos del usuario
        user.email = self.validated_data['email']
        
        print(f"Creando usuario con: email={user.email}")  # Debug log
        
        # Generar username usando el adaptador
        if hasattr(adapter, 'generate_unique_username'):
            user.username = adapter.generate_unique_username([user.email])
        else:
            # Fallback, when the adapter has no such method
            user.username = user.email.split('@')[0]
        
        # The password
        user.set_password(self.validated_data['password1'])
        
        # Save through the adapter, which calls our own save_user
        user = adapter.save_user(request, user, self)
        
        # Set the address up for confirmation
        setup_user_email(request, user, [])
        
        return user


class CustomPasswordResetForm(PasswordResetForm):
    def save(self, domain_override=None, subject_template_name='registration/password_reset_subject.txt',
             email_template_name='registration/password_reset_email.html',
             use_https=False, token_generator=None, from_email=None, request=None,
             html_email_template_name=None, extra_email_context=None):
        """
        Override save() to use FRONTEND_URL for domain and protocol,
        and generate UID in base36 format (allauth compatible).
        """
        # Parse FRONTEND_URL to get domain and protocol
        frontend_url = settings.FRONTEND_URL
        parsed = urlparse(frontend_url)
        domain_override = parsed.netloc
        use_https = parsed.scheme == 'https'
        
        # Import allauth user_pk_to_url_str for base36 encoding
        from allauth.account.utils import user_pk_to_url_str
        from allauth.account.forms import default_token_generator as allauth_token_generator
        
        # Use allauth's token generator if not provided
        if token_generator is None:
            token_generator = allauth_token_generator
        
        # Override the template context to use base36 UID
        # We need to send the email ourselves to control the UID format
        from django.core.mail import send_mail
        from django.template import loader
        
        for user in self.get_users(self.cleaned_data["email"]):
            # Generate base36 UID (allauth format)
            uid = user_pk_to_url_str(user)
            token = token_generator.make_token(user)
            
            context = {
                'email': user.email,
                'domain': domain_override,
                'protocol': 'https' if use_https else 'http',
                'uid': uid,
                'user': user,
                'token': token,
            }
            
            if extra_email_context is not None:
                context.update(extra_email_context)
            
            subject = loader.render_to_string(subject_template_name, context)
            subject = ''.join(subject.splitlines())
            body = loader.render_to_string(email_template_name, context)
            
            send_mail(
                subject,
                body,
                from_email,
                [user.email],
                fail_silently=False,
            )


class CustomPasswordResetSerializer(DefaultPasswordResetSerializer):
    @property
    def password_reset_form_class(self):
        return CustomPasswordResetForm
