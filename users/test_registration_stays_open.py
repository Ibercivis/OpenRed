"""The API's own registration endpoint stays open, on purpose.

The web app no longer offers it: a newcomer there signs in with an Ibercivis
account. The Flutter client still creates accounts here, so this route is not
dead code, and LOCAL-DEV.md carries the reasoning.

This case exists so that removing the route is a decision rather than a tidy-up.
"""
from django.test import TestCase
from rest_framework.test import APIClient


class RegistrationStaysOpen(TestCase):
    def test_the_endpoint_answers_rather_than_404(self):
        response = APIClient().post('/dj-rest-auth/registration/', {}, format='json')

        # 400 for an empty body: the route is there and the serializer refused it.
        self.assertEqual(response.status_code, 400, response.content)
