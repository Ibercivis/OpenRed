# Configuración de Amazon SES para OpenRed

Este documento explica cómo configurar Amazon Simple Email Service (SES) para el envío de correos electrónicos en OpenRed.

## 📋 Requisitos previos

1. **Cuenta de AWS** con acceso a Amazon SES
2. **Dominio verificado** en Amazon SES (o al menos un email verificado para testing)
3. **Credenciales de AWS** (Access Key ID y Secret Access Key)

## 🔧 Configuración paso a paso

### 1. Configurar Amazon SES en AWS Console

#### Verificar dominio o email
1. Ve a [Amazon SES Console](https://console.aws.amazon.com/ses/)
2. En el panel izquierdo, selecciona **"Verified identities"**
3. Haz clic en **"Create identity"**
4. Selecciona **"Domain"** (recomendado) o **"Email address"**
5. Introduce tu dominio (ej: `openred.com`) o email
6. Sigue las instrucciones para verificar la propiedad

#### Solicitar salida del Sandbox (Producción)
Por defecto, las cuentas nuevas están en "Sandbox mode":
- Solo puedes enviar a emails verificados
- Límite de 200 emails/día
- Máximo 1 email/segundo

Para producción:
1. Ve a **"Account dashboard"** en SES
2. Haz clic en **"Request production access"**
3. Completa el formulario explicando tu caso de uso
4. AWS revisará tu solicitud (suele tomar 24-48h)

### 2. Crear credenciales de AWS

#### Opción A: Usuario IAM (Recomendado)
1. Ve a [IAM Console](https://console.aws.amazon.com/iam/)
2. Crea un nuevo usuario: **"openred-ses-user"**
3. Asigna la policy: `AmazonSESFullAccess` (o una más restrictiva)
4. Genera **Access Key** y **Secret Key**

#### Política IAM restrictiva (más segura):
```json
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": [
                "ses:SendEmail",
                "ses:SendRawEmail",
                "ses:GetSendQuota",
                "ses:GetSendStatistics"
            ],
            "Resource": "*"
        }
    ]
}
```

### 3. Configurar variables de entorno

Copia el archivo `env_example` a `.env` y configura:

```bash
# Amazon SES Email Configuration
AWS_ACCESS_KEY_ID=AKIA...
AWS_SECRET_ACCESS_KEY=wJalrXUt...
AWS_SES_REGION_NAME=us-east-1
AWS_SES_CONFIGURATION_SET=  # Opcional

# Email settings
DEFAULT_FROM_EMAIL=noreply@yourdomain.com
```

**⚠️ Importante:** 
- Nunca commites credenciales en el código
- Usa IAM roles en producción cuando sea posible
- El email `DEFAULT_FROM_EMAIL` debe estar en un dominio verificado

### 4. Verificar configuración

Ejecuta el comando de verificación:

```bash
python manage.py check_ses
```

Para enviar un correo de prueba:

```bash
python manage.py check_ses --send-test-email --to-email=tu-email@example.com
```

## 🌍 Regiones recomendadas

- **us-east-1** (N. Virginia): La más común, mejor soporte
- **eu-west-1** (Irlanda): Para usuarios europeos
- **ap-southeast-2** (Sydney): Para usuarios de Asia-Pacífico

## 📊 Monitoreo y métricas

### Métricas importantes:
- **Bounce rate**: Emails que rebotaron (objetivo: <5%)
- **Complaint rate**: Marcados como spam (objetivo: <0.1%)
- **Delivery rate**: Emails entregados exitosamente

### Configurar notificaciones:
1. En SES Console, ve a **"Configuration sets"**
2. Crea un configuration set
3. Configura destinos para métricas (SNS, CloudWatch)
4. Usa el configuration set en settings: `AWS_SES_CONFIGURATION_SET`

## 🛠️ Troubleshooting

### Error: "Email address not verified"
- **Causa**: Estás en Sandbox mode o el dominio no está verificado
- **Solución**: Verifica el dominio/email o solicita production access

### Error: "Invalid credentials"
- **Causa**: Access Key/Secret Key incorrectos
- **Solución**: Verifica las credenciales en IAM Console

### Error: "Rate exceeded"
- **Causa**: Superaste el límite de envío
- **Solución**: Configura `AWS_SES_AUTO_THROTTLE` o solicita aumento de límites

### Emails van a spam
- **Causa**: Falta de autenticación DKIM/SPF
- **Solución**: Configura registros DNS según las instrucciones de SES

## 🔐 Mejores prácticas de seguridad

1. **Usa roles IAM** en lugar de credenciales en producción
2. **Rota credenciales** regularmente
3. **Aplica principio de menor privilegio** en políticas IAM
4. **Monitorea actividad** con CloudTrail
5. **Configura alertas** para métricas anómalas

## 📈 Escalabilidad

Para grandes volúmenes:
- Solicita aumento de límites en AWS Support
- Usa SQS para encolar emails
- Implementa retry logic para fallos temporales
- Considera múltiples regiones para redundancia

## 🧪 Testing

### Desarrollo local:
El proyecto usa `console` backend si las credenciales AWS no están configuradas.

### Testing automatizado:
```python
from django.test import TestCase
from django.core import mail

class EmailTestCase(TestCase):
    def test_email_sending(self):
        # Los emails se almacenan en mail.outbox durante tests
        mail.send_mail('Test', 'Content', 'from@example.com', ['to@example.com'])
        self.assertEqual(len(mail.outbox), 1)
```

## 📚 Enlaces útiles

- [Amazon SES Developer Guide](https://docs.aws.amazon.com/ses/)
- [django-ses Documentation](https://github.com/django-ses/django-ses)
- [AWS SES Pricing](https://aws.amazon.com/ses/pricing/)
- [SES Best Practices](https://docs.aws.amazon.com/ses/latest/dg/best-practices.html)
