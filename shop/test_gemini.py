from unittest.mock import patch, Mock
from django.test import SimpleTestCase, override_settings
from .agents import AgentError
from .llm_graph import make_model, provider_error


class GeminiProviderTests(SimpleTestCase):
    @override_settings(GEMINI_API_KEY='')
    def test_missing_key_has_actionable_error(self):
        with self.assertRaisesMessage(AgentError, 'Set GEMINI_API_KEY'):
            make_model('gemini')

    @override_settings(GEMINI_API_KEY='test-not-real', GEMINI_MODEL='gemini-3.5-flash-lite')
    @patch('langchain_google_genai.ChatGoogleGenerativeAI')
    def test_provider_receives_server_key_and_bounded_timeout(self, provider):
        make_model('gemini')
        self.assertEqual(provider.call_args.kwargs['google_api_key'], 'test-not-real')
        self.assertEqual(provider.call_args.kwargs['max_retries'], 0)
        self.assertNotIn('base_url', provider.call_args.kwargs)

    def test_provider_errors_never_expose_raw_secrets(self):
        for error, expected in [('429 secret-key', 'quota'), ('API_KEY_INVALID secret-key', 'key'), ('model not found secret-key', 'model'), ('timed out secret-key', 'timed out'), ('secret-key', 'failed')]:
            message = provider_error(RuntimeError(error))
            self.assertIn(expected, message)
            self.assertNotIn('secret-key', message)

    @override_settings(GEMINI_API_KEY='test-not-real')
    def test_real_client_and_tool_schema_initialize_without_network(self):
        model = make_model('gemini')
        bound = model.bind_tools([{'type': 'function', 'function': {'name': 'lookup', 'description': 'Lookup a product', 'parameters': {'type': 'object', 'properties': {'query': {'type': 'string'}}, 'required': ['query']}}}])
        self.assertIsNotNone(bound)
