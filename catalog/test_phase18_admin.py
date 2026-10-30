"""Phase 18: every model in a project app is registered in Django admin and its pages load."""
from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

PROJECT_ROOT = Path(settings.BASE_DIR).resolve()
SENSITIVE_FIELDS = {'otp_code', 'otp_hash', 'verification_token', 'email_verification_token', 'token_hash', 'gateway_signature'}


def project_models():
    for config in apps.get_app_configs():
        path = Path(config.path).resolve()
        if PROJECT_ROOT in path.parents and 'site-packages' not in path.parts:
            yield from config.get_models()


class AdminRegistrationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.superuser = get_user_model().objects.create_superuser('phase18admin', 'p18@example.test', 'unused-Pass-123')

    def setUp(self):
        self.client.force_login(self.superuser)

    def test_project_models_found(self):
        labels = {m._meta.app_label for m in project_models()}
        self.assertTrue({'accounts', 'catalog', 'notifications'} <= labels, labels)

    def test_every_project_model_registered(self):
        missing = [m._meta.label for m in project_models() if m not in admin.site._registry]
        self.assertEqual(missing, [])

    def test_auth_and_token_present(self):
        self.assertIn(get_user_model(), admin.site._registry)
        self.assertIn(apps.get_model('auth', 'Group'), admin.site._registry)
        self.assertIn(apps.get_model('authtoken', 'TokenProxy'), admin.site._registry)

    def test_changelist_and_add_pages_load(self):
        for model in project_models():
            opts = model._meta
            with self.subTest(model=opts.label):
                url = reverse(f'admin:{opts.app_label}_{opts.model_name}_changelist')
                self.assertEqual(self.client.get(url).status_code, 200, url)
                model_admin = admin.site._registry[model]
                add_url = reverse(f'admin:{opts.app_label}_{opts.model_name}_add')
                expected = 200 if model_admin.has_add_permission(self.client.get(url).wsgi_request) else 403
                self.assertEqual(self.client.get(add_url).status_code, expected, add_url)

    def test_sensitive_fields_not_in_admin_forms(self):
        for model in project_models():
            model_admin = admin.site._registry[model]
            request = self.client.get('/admin/').wsgi_request
            request.user = self.superuser
            with self.subTest(model=model._meta.label):
                fields = set(model_admin.get_fields(request))
                self.assertFalse(fields & SENSITIVE_FIELDS, fields & SENSITIVE_FIELDS)