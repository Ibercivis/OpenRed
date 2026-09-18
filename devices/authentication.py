"""
Device token authentication for headless measurement ingestion.

Headless devices (e.g. an M5Stack) cannot carry a user's password/token, so they
authenticate with a per-device ingest token sent in the ``X-Device-Token`` header.
The server stores only the SHA-256 hash of the token (see Device.issue_ingest_token),
looks the device up by that hash, and authenticates the request AS the device owner
while exposing the device itself via ``request.auth``.

This is intended ONLY for the measurement ingest endpoint. It returns ``None`` when
the header is absent, so the normal user-token / session auth still applies to the
rest of the API.
"""
from rest_framework import authentication, exceptions

from .models import Device


class DeviceTokenAuthentication(authentication.BaseAuthentication):
    """Authenticate a request from a registered device via its ingest token."""

    header = 'HTTP_X_DEVICE_TOKEN'

    def authenticate(self, request):
        raw_token = request.META.get(self.header)
        if not raw_token:
            # No device token present -> let other authenticators handle it.
            return None

        token_hash = Device.hash_ingest_token(raw_token)
        try:
            device = Device.objects.select_related('owner').get(
                ingest_token_hash=token_hash
            )
        except Device.DoesNotExist:
            raise exceptions.AuthenticationFailed('Token de dispositivo inválido.')

        if not device.is_active:
            raise exceptions.AuthenticationFailed('El dispositivo está inactivo.')
        if device.owner is None:
            raise exceptions.AuthenticationFailed('El dispositivo no tiene propietario.')

        # request.user = owner, request.auth = device
        return (device.owner, device)
