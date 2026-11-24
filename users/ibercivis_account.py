"""Signing in with an Ibercivis account: redeeming the code the web app brings back.

LOCAL-DEV.md describes the service and the accounts this creates. The browser
starts the flow and comes back to the React app carrying an authorization code,
which is redeemed here, against the client secret this API holds -- so no token
of the service's ever reaches a browser.

allauth ships the OpenID Connect provider and dj-rest-auth ships the view that
redeems an authorization code, as it does for Google. What is here is the part
neither does on its own, and nothing more:

* dj-rest-auth builds the adapter as `adapter_class(request)`, while allauth's
  OpenID Connect adapter also needs the provider's id.
* the PKCE verifier. See `VerifierForwardingClient` below.
* allauth's OpenID Connect adapter, in the release pinned here, reads the
  account's details from a second request to `userinfo`, which is a plain JSON
  body with nothing signed about it. `complete_login` below reads them from the
  ID token instead and verifies it -- with allauth's own `verify_and_decode`,
  the function behind its Google and Apple providers, so the checking is the
  library's even though the decision is not.
* only the claims this API names are kept, so what is stored about a person does
  not depend on what the service decides to put in the token. In particular
  nothing is stored about *how* somebody signed in there.
* dj-rest-auth also accepts a bare `access_token` instead of a code. Here only a
  code is accepted, so every sign-in goes through the token request this API
  makes itself, with its own secret.
"""
import functools

from allauth.socialaccount.internal.jwtkit import lookup_kid_jwk, verify_and_decode
from allauth.socialaccount.models import SocialApp
from allauth.socialaccount.providers.oauth2.client import OAuth2Client, OAuth2Error
from allauth.socialaccount.providers.openid_connect.views import OpenIDConnectOAuth2Adapter
from dj_rest_auth.registration.serializers import SocialLoginSerializer
from dj_rest_auth.registration.views import SocialLoginView
from django.conf import settings
from django.utils.translation import gettext_lazy as _
from rest_framework import serializers
from rest_framework.exceptions import NotFound


# dj-rest-auth's own message, so it translates the same way.
ADDRESS_IN_USE = _('User is already registered with this e-mail address.')


# The ID token claims kept on the SocialAccount. `sub` is its uid, `email` and
# `email_verified` make its address; `voperson_id` is the same identifier, under
# the name the federation gives it. Nothing else is stored, and in particular
# nothing that says how the person signed in at the service.
KEPT_CLAIMS = ('sub', 'voperson_id', 'email', 'email_verified')


class IbercivisAccountAdapter(OpenIDConnectOAuth2Adapter):
    def __init__(self, request):
        super().__init__(request, settings.IBERCIVIS_ACCOUNT_PROVIDER)

    def complete_login(self, request, app, token, **kwargs):
        # The token endpoint's own answer, which dj-rest-auth passes through:
        # the ID token is in it, and `parse_token` above drops it.
        id_token = (kwargs.get('response') or {}).get('id_token')
        if not id_token:
            # An answer with no ID token in it: the client is not registered for
            # the `openid` scope, or the service is not the one we think. Either
            # way it is a refusal rather than a server error.
            raise OAuth2Error('The token endpoint returned no ID token')
        claims = verify_and_decode(
            credential=id_token,
            keys_url=self.openid_config['jwks_uri'],
            issuer=self.openid_config['issuer'],
            audience=app.client_id,
            lookup_kid=lookup_kid_jwk,
        )
        return self.get_provider().sociallogin_from_response(
            request, {claim: claims[claim] for claim in KEPT_CLAIMS if claim in claims},
        )


class VerifierForwardingClient(OAuth2Client):
    """Carries the PKCE verifier into the token request.

    allauth's client sends a verifier when it is handed one, and the view above
    hands it the one the browser kept. Without it the service refuses the
    exchange: a code obtained in a browser is redeemed with the verifier for the
    challenge that obtained it.
    """

    def __init__(self, *args, code_verifier, **kwargs):
        super().__init__(*args, **kwargs)
        self.code_verifier = code_verifier

    def get_access_token(self, code, pkce_code_verifier=None):
        return super().get_access_token(code, pkce_code_verifier=self.code_verifier)


class IbercivisAccountLoginSerializer(SocialLoginSerializer):
    # Removed, so a token obtained anywhere else cannot be handed in instead of a code.
    access_token = None
    id_token = None
    code = serializers.CharField()
    code_verifier = serializers.CharField()

    def validate(self, attrs):
        try:
            return super().validate(attrs)
        except OAuth2Error as refused:
            # An ID token that fails its checks: signature, issuer, audience, expiry.
            # dj-rest-auth lets this one through as a server error.
            raise serializers.ValidationError(_('Incorrect value')) from refused
        except serializers.ValidationError as refused:
            # Somebody here already holds the address. dj-rest-auth refuses it in a
            # translated sentence; it is moved under `email` so the web app can tell
            # this refusal from the others whatever language it is in.
            if ADDRESS_IN_USE in [str(message) for message in refused.detail]:
                raise serializers.ValidationError({'email': [ADDRESS_IN_USE]}) from refused
            raise


class IbercivisAccountLoginView(SocialLoginView):
    adapter_class = IbercivisAccountAdapter
    serializer_class = IbercivisAccountLoginSerializer

    @property
    def callback_url(self):
        # The code was issued to the web app's callback page, so the token
        # request has to name that address again. Pinned here rather than taken
        # from the request: whoever posts a code does not choose it.
        return settings.IBERCIVIS_ACCOUNT_REDIRECT_URI

    @property
    def client_class(self):
        # Read when the serializer redeems the code, after its fields have been validated.
        return functools.partial(
            VerifierForwardingClient, code_verifier=self.request.data['code_verifier'],
        )

    def post(self, request, *args, **kwargs):
        try:
            return super().post(request, *args, **kwargs)
        except SocialApp.DoesNotExist as unconfigured:
            # No IBERCIVIS_ACCOUNT_ISSUER: the way in does not exist on this deployment.
            raise NotFound() from unconfigured
