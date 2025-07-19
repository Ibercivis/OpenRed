from django.core.management.base import BaseCommand
from django.core.mail import send_mail
from django.conf import settings
from django.utils import timezone


class Command(BaseCommand):
    help = 'Envía un correo de prueba usando Amazon SES'

    def add_arguments(self, parser):
        parser.add_argument(
            'to_email',
            type=str,
            help='Dirección de correo electrónico de destino',
        )
        parser.add_argument(
            '--subject',
            type=str,
            default='Correo de prueba desde OpenRed',
            help='Asunto del correo (opcional)',
        )
        parser.add_argument(
            '--message',
            type=str,
            help='Mensaje personalizado (opcional)',
        )

    def handle(self, *args, **options):
        to_email = options['to_email']
        subject = options['subject']
        
        # Mensaje por defecto o personalizado
        if options['message']:
            message = options['message']
        else:
            message = f"""
Hola,

Este es un correo de prueba enviado desde OpenRed usando Amazon SES.

Detalles del envío:
- Fecha y hora: {timezone.now().strftime('%d/%m/%Y %H:%M:%S')}
- Remitente: {settings.DEFAULT_FROM_EMAIL}
- Destinatario: {to_email}
- Backend de email: {settings.EMAIL_BACKEND}

Si recibes este mensaje, significa que Amazon SES está configurado correctamente.

Saludos,
El equipo de OpenRed
            """

        self.stdout.write('📤 Enviando correo de prueba...')
        self.stdout.write(f'   De: {settings.DEFAULT_FROM_EMAIL}')
        self.stdout.write(f'   Para: {to_email}')
        self.stdout.write(f'   Asunto: {subject}')

        try:
            send_mail(
                subject=subject,
                message=message.strip(),
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[to_email],
                fail_silently=False,
            )
            
            self.stdout.write(self.style.SUCCESS(f'✅ Correo enviado exitosamente a {to_email}'))
            self.stdout.write('   Revisa tu bandeja de entrada (y la carpeta de spam)')
            
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'❌ Error al enviar correo: {e}'))
            
            # Sugerencias según el tipo de error
            error_str = str(e).lower()
            if 'email address not verified' in error_str:
                self.stdout.write(self.style.WARNING('💡 El email de origen no está verificado en Amazon SES'))
            elif 'rate exceeded' in error_str:
                self.stdout.write(self.style.WARNING('💡 Has excedido el límite de envío de Amazon SES'))
            elif 'credentials' in error_str:
                self.stdout.write(self.style.WARNING('💡 Verifica tus credenciales de AWS en el archivo .env'))
