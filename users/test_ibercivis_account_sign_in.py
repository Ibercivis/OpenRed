"""Signing in with an Ibercivis account, through `POST /dj-rest-auth/ibercivis/`;
the flow is described in LOCAL-DEV.md.

No case talks to the network. The three answers the sign-in service has to give
for this -- its discovery document, its token endpoint and its signing keys --
are played by `TheService` below, which signs ID tokens with a key made for the
test run.
"""
import json
import time
from unittest import mock

import jwt
from allauth.account.models import EmailAddress
from allauth.socialaccount.models import SocialAccount
from cryptography.hazmat.primitives.asymmetric import rsa
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

ISSUER = 'http://ibercivis.test/realms/citsci'
CLIENT_ID = 'openred'
REDIRECT_URI = 'http://openred.test/auth/ibercivis/callback'
TOKEN_ENDPOINT = f'{ISSUER}/protocol/openid-connect/token'
JWKS_URI = f'{ISSUER}/protocol/openid-connect/certs'

SIGN_IN = '/dj-rest-auth/ibercivis/'
WHO_AM_I = '/dj-rest-auth/user/'
PASSWORD_SIGN_IN = '/dj-rest-auth/login/'

IBERCIVIS_ACCOUNT = {
    'openid_connect': {
        'SCOPE': ['openid', 'email'],
        'APPS': [{
            'provider_id': 'ibercivis',
            'name': 'Ibercivis',
            'client_id': CLIENT_ID,
            'secret': 'not-a-secret',
            'settings': {'server_url': ISSUER},
        }],
    },
}

# What the service says in an ID token about somebody who has just signed in.
# The identifier is the only thing it promises never changes, and nothing in it
# says how the person proved who they are: a Google account, an ORCID, a
# password.
IBERCIVIS_ACCOUNT_ID = '3d7ba8aa-1f1e-4b0e-9a3f-1d2c3b4a5e6f@ibercivis.es'
SAYS = {
    'sub': IBERCIVIS_ACCOUNT_ID,
    'voperson_id': IBERCIVIS_ACCOUNT_ID,
    'email': 'arrival@openred.invalid',
    'email_verified': True,
    'acr': '1',
}

# Passed as the ID token to say the token endpoint answers without one at all.
NO_ID_TOKEN = ''

SIGNING_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
A_KEY_THE_SERVICE_NEVER_HAD = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def an_id_token(key=SIGNING_KEY, **claims):
    now = int(time.time())
    body = {'iss': ISSUER, 'aud': CLIENT_ID, 'iat': now, 'exp': now + 300, **SAYS, **claims}
    return jwt.encode(body, key, algorithm='RS256', headers={'kid': 'the-key'})


class Answer:
    def __init__(self, body, status_code=200):
        self.status_code = status_code
        self.text = json.dumps(body)
        self.content = self.text.encode()
        self.headers = {'content-type': 'application/json'}
        self._body = body

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise AssertionError(f'{self.status_code} was not expected here')


class TheService:
    """The sign-in service, as far as allauth's requests session can tell."""

    def __init__(self, id_token):
        self.id_token = id_token
        self.token_requests = []
        self.credentials = []
        self.asked_for = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def get(self, url, **_):
        self.asked_for.append(url)
        if url == f'{ISSUER}/.well-known/openid-configuration':
            return Answer({
                'issuer': ISSUER,
                'authorization_endpoint': f'{ISSUER}/protocol/openid-connect/auth',
                'token_endpoint': TOKEN_ENDPOINT,
                'userinfo_endpoint': f'{ISSUER}/protocol/openid-connect/userinfo',
                'jwks_uri': JWKS_URI,
                'token_endpoint_auth_methods_supported': ['client_secret_basic', 'client_secret_post'],
            })
        if url == JWKS_URI:
            jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(SIGNING_KEY.public_key()))
            return Answer({'keys': [{**jwk, 'kid': 'the-key', 'use': 'sig', 'alg': 'RS256'}]})
        raise AssertionError(f'nothing here should ask for {url}')

    def request(self, method, url, data=None, auth=None, **_):
        if (method, url) != ('POST', TOKEN_ENDPOINT):
            raise AssertionError(f'nothing here should ask for {method} {url}')
        self.token_requests.append(data)
        self.credentials.append(auth)
        answer = {
            'access_token': 'an-access-token',
            'token_type': 'Bearer',
            'expires_in': 300,
        }
        if self.id_token is not None:
            answer['id_token'] = self.id_token
        return Answer(answer)


@override_settings(
    SOCIALACCOUNT_PROVIDERS=IBERCIVIS_ACCOUNT,
    IBERCIVIS_ACCOUNT_REDIRECT_URI=REDIRECT_URI,
)
class SignInCase(TestCase):
    def sign_in(self, id_token=None, **posted):
        self.service = TheService(an_id_token() if id_token is None else id_token or None)
        body = {'code': 'an-authorization-code', 'code_verifier': 'the-verifier', **posted}
        body = {name: value for name, value in body.items() if value is not None}
        with mock.patch(
            'allauth.socialaccount.adapter.DefaultSocialAccountAdapter.get_requests_session',
            return_value=self.service,
        ):
            return APIClient().post(SIGN_IN, body, format='json')

    def me(self, key):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f'Token {key}')
        return client.get(WHO_AM_I).json()


class RedeemingTheCode(SignInCase):
    def test_a_valid_code_answers_with_the_token_a_password_sign_in_answers_with(self):
        response = self.sign_in()

        self.assertEqual(response.status_code, 200, response.content)
        self.assertIn('key', response.json())

        self.assertEqual(self.me(response.json()['key'])['email'], SAYS['email'])

    def test_the_token_request_carries_the_verifier_and_the_pinned_callback(self):
        self.sign_in(code_verifier='what-the-browser-kept')

        [sent] = self.service.token_requests
        self.assertEqual(sent['code'], 'an-authorization-code')
        self.assertEqual(sent['code_verifier'], 'what-the-browser-kept')
        self.assertEqual(sent['redirect_uri'], REDIRECT_URI)
        # The client authenticates with HTTP Basic, which is what the service
        # announces first in `token_endpoint_auth_methods_supported` and what
        # allauth picks from it. The secret is therefore not in the body.
        [credentials] = self.service.credentials
        self.assertEqual((credentials.username, credentials.password), (CLIENT_ID, 'not-a-secret'))
        self.assertNotIn('client_secret', sent)

    def test_the_callback_cannot_be_chosen_by_whoever_posts(self):
        self.sign_in(redirect_uri='http://elsewhere.test/callback', callback_url='http://elsewhere.test/')

        [sent] = self.service.token_requests
        self.assertEqual(sent['redirect_uri'], REDIRECT_URI)

    def test_the_identity_comes_from_the_token_and_userinfo_is_never_asked(self):
        self.sign_in()

        self.assertNotIn(f'{ISSUER}/protocol/openid-connect/userinfo', self.service.asked_for)
        self.assertIn(JWKS_URI, self.service.asked_for)


class RefusingTheIdToken(SignInCase):
    """Every refusal leaves no account behind, and no token."""

    def assert_refused(self, response):
        self.assertEqual(response.status_code, 400, response.content)
        self.assertNotIn('key', response.json())
        self.assertFalse(User.objects.filter(email=SAYS['email']).exists())

    def test_a_token_signed_by_another_key_is_refused(self):
        self.assert_refused(self.sign_in(an_id_token(key=A_KEY_THE_SERVICE_NEVER_HAD)))

    def test_a_token_issued_to_another_application_is_refused(self):
        self.assert_refused(self.sign_in(an_id_token(aud='lostatnight')))

    def test_a_token_from_another_issuer_is_refused(self):
        self.assert_refused(self.sign_in(an_id_token(iss='http://impostor.test/realms/citsci')))

    def test_an_expired_token_is_refused(self):
        an_hour_ago = int(time.time()) - 3600
        self.assert_refused(self.sign_in(an_id_token(iat=an_hour_ago - 300, exp=an_hour_ago)))

    def test_an_answer_with_no_id_token_in_it_is_refused(self):
        # What a client not registered for the `openid` scope gets back, and what
        # something that is not this service might answer. There is nothing to
        # check, so there is nothing to accept.
        self.assert_refused(self.sign_in(id_token=NO_ID_TOKEN))


class RefusingTheRequest(SignInCase):
    def test_an_access_token_instead_of_a_code_is_refused(self):
        response = self.sign_in(code=None, access_token='a-token-from-somewhere-else')

        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn('code', response.json())
        self.assertEqual(self.service.token_requests, [])

    def test_a_code_without_its_verifier_is_refused(self):
        response = self.sign_in(code_verifier=None)

        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn('code_verifier', response.json())
        self.assertEqual(self.service.token_requests, [])

    @override_settings(SOCIALACCOUNT_PROVIDERS={})
    def test_without_an_ibercivis_account_configured_the_address_does_not_exist(self):
        self.assertEqual(self.sign_in().status_code, 404)


class TheAccountItCreates(SignInCase):
    def arrival(self, id_token=None):
        response = self.sign_in(id_token)
        self.assertEqual(response.status_code, 200, response.content)
        return User.objects.get(email=SAYS['email'])

    def test_it_has_no_password_and_no_privileges(self):
        user = self.arrival()

        self.assertFalse(user.has_usable_password())
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)

    def test_it_is_keyed_by_the_identifier(self):
        user = self.arrival()

        [account] = SocialAccount.objects.filter(user=user)
        self.assertEqual((account.provider, account.uid), ('ibercivis', IBERCIVIS_ACCOUNT_ID))

    def test_it_stores_nothing_about_how_the_person_signed_in_there(self):
        # Claims a mapper on the service could add tomorrow. What is stored is
        # what this API names, not whatever the ID token happens to carry.
        user = self.arrival(an_id_token(identity_provider='google', amr=['google'], name='Arrival'))

        [account] = SocialAccount.objects.filter(user=user)
        self.assertEqual(account.extra_data, {
            'sub': IBERCIVIS_ACCOUNT_ID,
            'voperson_id': IBERCIVIS_ACCOUNT_ID,
            'email': SAYS['email'],
            'email_verified': True,
        })

    def test_the_second_sign_in_lands_on_the_same_account_whatever_the_address_says(self):
        first = self.sign_in().json()['key']
        second = self.sign_in(an_id_token(email='renamed@openred.invalid')).json()['key']

        self.assertEqual(Token.objects.get(key=first).user, Token.objects.get(key=second).user)
        self.assertEqual(User.objects.count(), 1)


class AnAddressAlreadyInUse(SignInCase):
    """Somebody who already signs in here with a password, arriving with the same address.

    They get no second account, and nothing connects the two. The Google sign-in
    this project has always had does connect them, and keeps doing it:
    users/adapters.py carries the reason the two answers differ.
    """

    def setUp(self):
        self.veteran = User.objects.create_user('veteran', SAYS['email'], 'the-password-they-had')
        EmailAddress.objects.create(user=self.veteran, email=SAYS['email'], verified=True, primary=True)

    def test_it_is_refused_with_a_reason_the_web_app_can_show(self):
        response = self.sign_in()

        self.assertEqual(response.status_code, 400, response.content)
        self.assertNotIn('key', response.json())
        # Under `email`, which nothing posted here carries. The module says why
        # that field and not another.
        self.assertEqual(list(response.json()), ['email'])

    def test_it_creates_nothing_and_connects_nothing(self):
        self.sign_in()

        self.assertEqual(list(User.objects.values_list('username', flat=True)), ['veteran'])
        self.assertFalse(SocialAccount.objects.exists())

    def test_the_existing_account_still_signs_in_with_its_password(self):
        self.sign_in()

        response = APIClient().post(
            PASSWORD_SIGN_IN,
            {'email': SAYS['email'], 'password': 'the-password-they-had'}, format='json',
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(Token.objects.get(key=response.json()['key']).user, self.veteran)


class WhoAmI(SignInCase):
    """`/dj-rest-auth/user/` names the Ibercivis account a user signs in with, if any.

    The profile screen the React app already has is what reads it, so no
    endpoint was added for this.
    """

    def test_an_account_from_an_ibercivis_sign_in_shows_its_identifier(self):
        self.assertEqual(
            self.me(self.sign_in().json()['key'])['ibercivis_account_id'], IBERCIVIS_ACCOUNT_ID,
        )

    def test_an_account_with_only_a_password_shows_none(self):
        user = User.objects.create_user('veteran', 'veteran@openred.invalid', 'a-password')

        self.assertIsNone(self.me(Token.objects.create(user=user).key)['ibercivis_account_id'])
