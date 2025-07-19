from django.contrib.auth.models import User
from rest_framework import serializers

class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ['id', 'username', 'email', 'date_joined', 'is_active']
        read_only_fields = ['id', 'username', 'date_joined', 'is_active']


# Serializador personalizado para registro
class CustomRegisterSerializer(serializers.Serializer):
    """
    Serializador simplificado para registro con solo email y contraseña.
    """
    email = serializers.EmailField()
    password1 = serializers.CharField(write_only=True, min_length=8)
    password2 = serializers.CharField(write_only=True, min_length=8)

    def validate(self, attrs):
        """
        Validar que las contraseñas coincidan.
        """
        if attrs['password1'] != attrs['password2']:
            raise serializers.ValidationError("Las contraseñas no coinciden.")
        return attrs

    def validate_email(self, value):
        """
        Validar que el email sea único.
        """
        if User.objects.filter(email=value).exists():
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
        Método requerido por el serializer base (no usado en registro).
        """
        raise NotImplementedError("Update not supported for registration")

    def get_cleaned_data(self):
        """
        Método esperado por allauth adapter para obtener datos limpios.
        """
        return {
            'email': self.validated_data['email'],
            'password1': self.validated_data['password1'],
        }

    def save(self, request=None, **kwargs):
        """
        Personalizar el método save para trabajar con allauth y dj-rest-auth.
        """
        # Si se pasa request como parámetro posicional (dj-rest-auth lo hace así)
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
            # Fallback si no existe el método
            user.username = user.email.split('@')[0]
        
        # Establecer contraseña
        user.set_password(self.validated_data['password1'])
        
        # Guardar usuario usando el adaptador (esto llamará a nuestro save_user)
        user = adapter.save_user(request, user, self)
        
        # Configurar email para verificación
        setup_user_email(request, user, [])
        
        return user
