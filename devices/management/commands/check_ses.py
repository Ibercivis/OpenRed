from django.core.management.base import BaseCommand
from django.core.mail import send_mail
from django.conf import settings
import boto3
from botocore.exceptions import ClientError, NoCredentialsError


class Command(BaseCommand):
    help = 'Verifica la configuración de Amazon SES y prueba el envío de correos'

    def add_arguments(self, parser):
        parser.add_argument(
            '--send-test-email',
            action='store_true',
            help='Envía un correo de prueba',
        )
        parser.add_argument(
            '--to-email',
            type=str,
            help='Email destinatario para la prueba',
        )

    def handle(self, *args, **options):
        self.stdout.write('🔍 Verificando configuración de Amazon SES...\n')
        
        # Verificar configuración básica
        self.check_configuration()
        
        # Verificar conexión con AWS
        self.check_aws_connection()
        
        # Verificar dominios verificados
        self.check_verified_domains()
        
        # Enviar correo de prueba si se solicita
        if options['send_test_email']:
            if not options['to_email']:
                self.stderr.write('❌ Debes especificar --to-email para enviar el correo de prueba')
                return
                
            self.send_test_email(options['to_email'])

    def check_configuration(self):
        """Verifica la configuración básica de SES en settings"""
        self.stdout.write('📋 Configuración actual:')
        
        # Verificar EMAIL_BACKEND
        email_backend = getattr(settings, 'EMAIL_BACKEND', None)
        if email_backend == 'django_ses.SESBackend':
            self.stdout.write(f'  ✅ EMAIL_BACKEND: {email_backend}')
        else:
            self.stdout.write(f'  ⚠️  EMAIL_BACKEND: {email_backend} (no es SES)')
        
        # Verificar variables AWS
        aws_access_key = getattr(settings, 'AWS_ACCESS_KEY_ID', None)
        aws_secret_key = getattr(settings, 'AWS_SECRET_ACCESS_KEY', None)
        aws_region = getattr(settings, 'AWS_SES_REGION_NAME', None)
        
        if aws_access_key:
            self.stdout.write(f'  ✅ AWS_ACCESS_KEY_ID: {aws_access_key[:8]}...')
        else:
            self.stdout.write('  ❌ AWS_ACCESS_KEY_ID: No configurado')
            
        if aws_secret_key:
            self.stdout.write(f'  ✅ AWS_SECRET_ACCESS_KEY: {aws_secret_key[:8]}...')
        else:
            self.stdout.write('  ❌ AWS_SECRET_ACCESS_KEY: No configurado')
            
        if aws_region:
            self.stdout.write(f'  ✅ AWS_SES_REGION_NAME: {aws_region}')
        else:
            self.stdout.write('  ❌ AWS_SES_REGION_NAME: No configurado')
            
        default_from = getattr(settings, 'DEFAULT_FROM_EMAIL', None)
        if default_from:
            self.stdout.write(f'  ✅ DEFAULT_FROM_EMAIL: {default_from}')
        else:
            self.stdout.write('  ❌ DEFAULT_FROM_EMAIL: No configurado')
        
        self.stdout.write('')

    def check_aws_connection(self):
        """Verifica la conexión con AWS SES"""
        self.stdout.write('🔌 Verificando conexión con AWS SES...')
        
        try:
            # Crear cliente SES
            client = boto3.client(
                'ses',
                region_name=getattr(settings, 'AWS_SES_REGION_NAME', 'us-east-1'),
                aws_access_key_id=getattr(settings, 'AWS_ACCESS_KEY_ID', None),
                aws_secret_access_key=getattr(settings, 'AWS_SECRET_ACCESS_KEY', None)
            )
            
            # Probar conexión obteniendo la cuota de envío
            response = client.get_send_quota()
            
            self.stdout.write('  ✅ Conexión exitosa con AWS SES')
            self.stdout.write(f'  📊 Cuota diaria: {response["Max24HourSend"]:.0f} emails')
            self.stdout.write(f'  📊 Cuota por segundo: {response["MaxSendRate"]:.0f} emails/seg')
            self.stdout.write(f'  📊 Enviados en 24h: {response["SentLast24Hours"]:.0f} emails')
            
        except NoCredentialsError:
            self.stderr.write('  ❌ Credenciales AWS no configuradas o inválidas')
        except ClientError as e:
            self.stderr.write(f'  ❌ Error de AWS: {e}')
        except Exception as e:
            self.stderr.write(f'  ❌ Error inesperado: {e}')
            
        self.stdout.write('')

    def check_verified_domains(self):
        """Verifica los dominios y emails verificados en SES"""
        self.stdout.write('📧 Verificando emails/dominios verificados...')
        
        try:
            client = boto3.client(
                'ses',
                region_name=getattr(settings, 'AWS_SES_REGION_NAME', 'us-east-1'),
                aws_access_key_id=getattr(settings, 'AWS_ACCESS_KEY_ID', None),
                aws_secret_access_key=getattr(settings, 'AWS_SECRET_ACCESS_KEY', None)
            )
            
            # Obtener emails verificados
            verified_emails = client.list_verified_email_addresses()
            if verified_emails['VerifiedEmailAddresses']:
                self.stdout.write('  ✅ Emails verificados:')
                for email in verified_emails['VerifiedEmailAddresses']:
                    self.stdout.write(f'    - {email}')
            else:
                self.stdout.write('  ⚠️  No hay emails verificados')
            
            # Obtener dominios verificados
            identities = client.list_identities()
            domains = [identity for identity in identities['Identities'] if '@' not in identity]
            if domains:
                self.stdout.write('  ✅ Dominios verificados:')
                for domain in domains:
                    self.stdout.write(f'    - {domain}')
            else:
                self.stdout.write('  ⚠️  No hay dominios verificados')
                
        except Exception as e:
            self.stderr.write(f'  ❌ Error verificando identidades: {e}')
            
        self.stdout.write('')

    def send_test_email(self, to_email):
        """Envía un correo de prueba"""
        self.stdout.write(f'📤 Enviando correo de prueba a {to_email}...')
        
        try:
            send_mail(
                subject='🧪 Correo de prueba - OpenRed SES',
                message='''
¡Hola!

Este es un correo de prueba enviado desde OpenRed usando Amazon SES.

Si recibes este mensaje, significa que la configuración de SES está funcionando correctamente.

Saludos,
El equipo de OpenRed
                '''.strip(),
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[to_email],
                fail_silently=False,
            )
            
            self.stdout.write('  ✅ Correo enviado exitosamente')
            self.stdout.write(f'  📧 Verifica la bandeja de entrada de {to_email}')
            
        except Exception as e:
            self.stderr.write(f'  ❌ Error enviando correo: {e}')
            
        self.stdout.write('')
