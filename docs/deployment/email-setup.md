# Configuración de AWS SES para emails

## 📧 Configurar Amazon SES

### **1. Verificar el dominio `ibercivis.es` en AWS SES**

1. Ir a AWS Console → Amazon SES → Verified identities
2. Click en "Create identity"
3. Seleccionar "Domain"
4. Introducir: `ibercivis.es`
5. AWS proporcionará registros DNS (DKIM, SPF, DMARC)
6. Añadir esos registros DNS en tu proveedor de dominio
7. Esperar verificación (puede tardar hasta 72h, normalmente minutos)

### **2. Verificar el email `noreply@ibercivis.es`**

1. AWS Console → Amazon SES → Verified identities
2. Click en "Create identity"
3. Seleccionar "Email address"
4. Introducir: `noreply@ibercivis.es`
5. AWS enviará un email de verificación a ese correo
6. Hacer click en el link del email para verificar

### **3. Salir del Sandbox Mode (importante para producción)**

Por defecto, AWS SES está en "Sandbox Mode" que solo permite:
- Enviar a emails verificados
- Límite de 200 emails/día

Para producción:
1. AWS Console → Amazon SES → Account dashboard
2. Click en "Request production access"
3. Completar el formulario:
   - **Mail Type**: Transactional
   - **Website URL**: https://map.open-red.es
   - **Use case description**: 
     ```
     Scientific project for radiation and light pollution monitoring.
     We send transactional emails only:
     - Account verification emails
     - Password reset emails
     - System notifications to registered users
     
     Estimated volume: 50-100 emails/day
     Users must explicitly register on our platform.
     We do not send marketing emails.
     ```
4. AWS responderá en 24-48h

### **4. Crear usuario IAM con permisos SES**

1. AWS Console → IAM → Users → Create user
2. Nombre: `openred-ses-user`
3. Permisos: Crear política personalizada:

```json
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": [
                "ses:SendEmail",
                "ses:SendRawEmail"
            ],
            "Resource": "*"
        }
    ]
}
```

4. Crear Access Key para el usuario
5. Guardar `AWS_ACCESS_KEY_ID` y `AWS_SECRET_ACCESS_KEY`

### **5. Configurar en el proyecto**

Editar `.env.prod`:

```bash
# Email Configuration (Amazon SES)
EMAIL_BACKEND=django_ses.SESBackend
DEFAULT_FROM_EMAIL=noreply@ibercivis.es
SERVER_EMAIL=noreply@ibercivis.es

# AWS SES Configuration
AWS_SES_REGION_NAME=eu-central-1
AWS_SES_REGION_ENDPOINT=email.eu-central-1.amazonaws.com
AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE  # Tu Access Key real
AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY  # Tu Secret Key real
```

### **6. Verificar instalación de dependencias**

```bash
pip list | grep django-ses
# Debería mostrar: django-ses==X.X.X

# Si no está instalado:
pip install django-ses
pip freeze > requirements.txt
```

### **7. Test de envío**

```python
# En Django shell
python manage.py shell

from django.core.mail import send_mail

send_mail(
    'Test email from OpenRed',
    'This is a test email from AWS SES.',
    'noreply@ibercivis.es',
    ['tu-email@ejemplo.com'],
    fail_silently=False,
)
```

## 🔍 Troubleshooting

### **Error: "Email address is not verified"**

- Verificar que `noreply@ibercivis.es` esté verificado en AWS SES
- Si estás en Sandbox Mode, el email destinatario también debe estar verificado

### **Error: "Invalid AWS credentials"**

- Verificar `AWS_ACCESS_KEY_ID` y `AWS_SECRET_ACCESS_KEY`
- Verificar que el usuario IAM tenga permisos `ses:SendEmail`

### **Error: "Daily sending quota exceeded"**

- Estás en Sandbox Mode (límite 200 emails/día)
- Solicitar salir del Sandbox Mode (ver paso 3)

### **Emails no llegan**

1. Verificar logs de AWS SES:
   - AWS Console → SES → Sending statistics
2. Verificar que el dominio tenga registros DNS correctos (SPF, DKIM, DMARC)
3. Verificar que no estén en spam

## 📊 Monitoreo

### **Ver estadísticas de envío:**

AWS Console → SES → Sending statistics
- Emails enviados
- Bounces (rebotes)
- Complaints (reportes de spam)

### **Configurar alertas:**

AWS Console → CloudWatch → Alarms
- Crear alerta si Bounce rate > 5%
- Crear alerta si Complaint rate > 0.1%

## 💰 Costos

- **Primeros 62,000 emails/mes**: GRATIS (si envías desde EC2)
- **Después**: $0.10 por cada 1,000 emails

Para 100 emails/día = 3,000 emails/mes → **Completamente GRATIS** ✅

## 🔐 Seguridad

- ✅ NO commitear las AWS keys a git (ya está en `.gitignore`)
- ✅ Usar usuario IAM con permisos mínimos (solo SES)
- ✅ Rotar las keys cada 90 días
- ✅ Habilitar MFA en cuenta AWS

## 📝 Checklist de producción

- [ ] Dominio `ibercivis.es` verificado en AWS SES
- [ ] Email `noreply@ibercivis.es` verificado
- [ ] Registros DNS configurados (SPF, DKIM, DMARC)
- [ ] Usuario IAM creado con permisos SES
- [ ] Access Keys guardadas en `.env.prod`
- [ ] Solicitud de salir del Sandbox enviada (si es necesario)
- [ ] Test de envío realizado con éxito
- [ ] Emails llegan correctamente y no van a spam
