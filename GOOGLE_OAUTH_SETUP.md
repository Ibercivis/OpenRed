# Guía: Configurar Google OAuth para OpenRed API

## ⚠️ Error: "Request to user info failed"

Este error ocurre porque la **Google People API** no está habilitada en tu proyecto de Google Cloud.

## 🔧 Solución paso a paso:

### 1. Habilitar Google People API

1. Ve a [Google Cloud Console](https://console.cloud.google.com/)
2. Selecciona tu proyecto
3. Ve a **APIs & Services** → **Library**
4. Busca "**Google People API**"
5. Click en "**Enable**"

### 2. Verificar credenciales OAuth 2.0

1. Ve a **APIs & Services** → **Credentials**
2. Verifica que tus **Authorized redirect URIs** incluyan:
   ```
   https://dev.ibercivis.es/accounts/google/login/callback/
   http://localhost:8000/accounts/google/login/callback/
   ```

### 3. Configurar la pantalla de consentimiento

1. Ve a **APIs & Services** → **OAuth consent screen**
2. Configura:
   - **User Type**: External (para testing)
   - **App name**: OpenRed
   - **User support email**: tu email
   - **Developer contact**: tu email
3. En **Scopes**, agrega:
   - `../auth/userinfo.email`
   - `../auth/userinfo.profile`
   - `openid`

### 4. Probar el endpoint

#### Opción A: Con access_token (recomendado para SPAs)

```bash
# 1. El frontend obtiene el access_token de Google usando Google Sign-In
# 2. Envía el token a tu backend:

curl -X POST https://dev.ibercivis.es:8000/dj-rest-auth/google/ \
  -H "Content-Type: application/json" \
  -d '{
    "access_token": "ya29.a0AfH6SMB..."
  }'
```

#### Opción B: Con código de autorización

```bash
curl -X POST https://dev.ibercivis.es:8000/dj-rest-auth/google/ \
  -H "Content-Type: application/json" \
  -d '{
    "code": "4/0AX4XfWh..."
  }'
```

### 5. Respuesta esperada

```json
{
  "key": "a1b2c3d4e5f6...",
  "user": {
    "id": 1,
    "email": "usuario@gmail.com",
    "username": "usuario_gmail_com",
    "first_name": "Usuario",
    "last_name": "Apellido"
  }
}
```

## 🧪 Probar desde el navegador

Simplemente ve a:
```
https://dev.ibercivis.es:8000/accounts/google/login/
```

Esto iniciará el flujo completo de OAuth y te redirigirá automáticamente.

## 📱 Implementación en el Frontend

### Con React + @react-oauth/google

```bash
npm install @react-oauth/google
```

**Opción 1: Usando useGoogleLogin (RECOMENDADO - obtiene access_token)**

```jsx
import { GoogleOAuthProvider, useGoogleLogin } from '@react-oauth/google';

// Wrap your app
function App() {
  return (
    <GoogleOAuthProvider clientId="870159312146-dlvmn6jcj4oo14knb5h4g48j5d2hb357.apps.googleusercontent.com">
      <LoginPage />
    </GoogleOAuthProvider>
  );
}

// Login component
function LoginPage() {
  const login = useGoogleLogin({
    onSuccess: async (tokenResponse) => {
      try {
        const response = await fetch('https://dev.ibercivis.es:8000/dj-rest-auth/google/', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({
            access_token: tokenResponse.access_token,
          }),
        });
        
        const data = await response.json();
        
        if (response.ok) {
          localStorage.setItem('authToken', data.key);
          console.log('User:', data.user);
          window.location.href = '/dashboard';
        } else {
          console.error('Login failed:', data);
        }
      } catch (error) {
        console.error('Error:', error);
      }
    },
    onError: (error) => console.log('Login Failed:', error),
  });

  return (
    <button onClick={() => login()}>
      Iniciar sesión con Google
    </button>
  );
}
```

**Opción 2: Usando GoogleLogin (obtiene credential/id_token)**

```jsx
import { GoogleOAuthProvider, GoogleLogin } from '@react-oauth/google';

function LoginPage() {
  const handleGoogleLogin = async (credentialResponse) => {
    const response = await fetch('https://dev.ibercivis.es:8000/dj-rest-auth/google/', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        id_token: credentialResponse.credential,  // Este es un JWT
      }),
    });
    
    const data = await response.json();
    
    if (data.key) {
      localStorage.setItem('authToken', data.key);
      window.location.href = '/dashboard';
    }
  };

  return (
    <GoogleLogin
      onSuccess={handleGoogleLogin}
      onError={() => console.log('Login Failed')}
    />
  );
}
```

**Nota**: La Opción 1 (useGoogleLogin) funciona directamente con el backend actual.
```

### Con Vue + vue3-google-login

```bash
npm install vue3-google-login
```

```vue
<template>
  <GoogleLogin
    :callback="handleGoogleLogin"
    client-id="870159312146-dlvmn6jcj4oo14knb5h4g48j5d2hb357.apps.googleusercontent.com"
  />
</template>

<script>
import { GoogleLogin } from 'vue3-google-login';

export default {
  components: { GoogleLogin },
  methods: {
    async handleGoogleLogin(response) {
      const res = await fetch('https://dev.ibercivis.es:8000/dj-rest-auth/google/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          access_token: response.credential
        })
      });
      
      const data = await res.json();
      if (data.key) {
        localStorage.setItem('auth_token', data.key);
        this.$router.push('/dashboard');
      }
    }
  }
};
</script>
```

## 🔍 Troubleshooting

### Error: "Invalid token"
- Verifica que el access_token no haya expirado
- Los tokens de Google típicamente expiran en 1 hora

### Error: "redirect_uri_mismatch"
- Verifica que las URIs de redirección en Google Cloud coincidan exactamente

### Error: "Access blocked: Authorization Error"
- Ve a OAuth consent screen y agrega tu email como "Test user"

## ✅ Checklist

- [ ] Google People API habilitada
- [ ] OAuth consent screen configurado
- [ ] Redirect URIs configurados correctamente
- [ ] Variables de entorno GOOGLE_CLIENT_ID y GOOGLE_CLIENT_SECRET configuradas
- [ ] Comando `python manage.py setup_google_oauth` ejecutado
- [ ] Servidor Django reiniciado

## 📚 Referencias

- [Google OAuth Documentation](https://developers.google.com/identity/protocols/oauth2)
- [django-allauth Google Provider](https://django-allauth.readthedocs.io/en/latest/providers.html#google)
- [dj-rest-auth Social Authentication](https://dj-rest-auth.readthedocs.io/en/latest/installation.html#social-authentication)
