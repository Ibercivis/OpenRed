# Login con Google en React - Implementación Directa

## 1. Instalar dependencia en tu proyecto React

```bash
npm install @react-oauth/google
```

## 2. Configurar el Provider (en App.js o _app.js)

```javascript
import { GoogleOAuthProvider } from '@react-oauth/google';

function App() {
  return (
    <GoogleOAuthProvider clientId="870159312146-dlvmn6jcj4oo14knb5h4g48j5d2hb357.apps.googleusercontent.com">
      {/* Tu app aquí */}
      <Routes>
        {/* ... tus rutas */}
      </Routes>
    </GoogleOAuthProvider>
  );
}

export default App;
```

## 3. Crear componente de Login (LoginButton.jsx)

```javascript
import { useGoogleLogin } from '@react-oauth/google';
import { useState } from 'react';

function LoginButton() {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const login = useGoogleLogin({
    onSuccess: async (tokenResponse) => {
      setLoading(true);
      setError(null);
      
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
          // Guardar el token de autenticación
          localStorage.setItem('authToken', data.key);
          
          // Mostrar info del usuario
          console.log('Login exitoso!', data.user);
          
          // Redirigir o actualizar estado
          window.location.href = '/dashboard';
        } else {
          setError(data);
          console.error('Login falló:', data);
        }
      } catch (err) {
        setError({ message: err.message });
        console.error('Error:', err);
      } finally {
        setLoading(false);
      }
    },
    onError: (error) => {
      console.error('Google Login Failed:', error);
      setError({ message: 'Error al iniciar sesión con Google' });
    },
  });

  return (
    <div>
      <button 
        onClick={() => login()} 
        disabled={loading}
        style={{
          padding: '10px 20px',
          fontSize: '16px',
          cursor: loading ? 'not-allowed' : 'pointer',
        }}
      >
        {loading ? 'Cargando...' : '🔐 Iniciar sesión con Google'}
      </button>
      
      {error && (
        <div style={{ color: 'red', marginTop: '10px' }}>
          Error: {JSON.stringify(error)}
        </div>
      )}
    </div>
  );
}

export default LoginButton;
```

## 4. Usar el componente

```javascript
import LoginButton from './components/LoginButton';

function LoginPage() {
  return (
    <div>
      <h1>Inicia sesión en OpenRed</h1>
      <LoginButton />
    </div>
  );
}
```

## 5. ¡Listo!

- **Abre tu app React** en el navegador
- **Click en el botón** de login con Google
- **Selecciona tu cuenta** de Google
- **El backend validará** el token y devolverá el token de Django
- **Se guardará** en localStorage como 'authToken'

## 6. Usar el token en peticiones futuras

```javascript
const authToken = localStorage.getItem('authToken');

fetch('https://dev.ibercivis.es:8000/api/missions/', {
  headers: {
    'Authorization': `Token ${authToken}`,
    'Content-Type': 'application/json',
  },
});
```

## Troubleshooting

Si ves errores de CORS, asegúrate de que en el backend (settings.py) tienes:

```python
CORS_ALLOWED_ORIGINS = [
    'http://localhost:3000',
    'https://map.open-red.es',
]
```

## Verificar que Google People API esté habilitada

https://console.cloud.google.com/apis/library/people.googleapis.com

Click en "ENABLE" si no lo está.
