from importlib.metadata import version, PackageNotFoundError
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = 'Validate dependencies/config. --live makes one minimal Gemini model request and may consume quota.'

    def add_arguments(self, parser):
        parser.add_argument('--live', action='store_true')

    def handle(self, *args, **options):
        self.stdout.write(f'Engine: {settings.AGENT_ENGINE}; mode: {settings.AI_MODE}')
        for name in ['langgraph', 'langchain-core', 'langchain-google-genai']:
            try:
                self.stdout.write(name + ': ' + version(name))
            except PackageNotFoundError:
                raise CommandError('Missing dependency: ' + name + '. Install requirements.txt.')
        self.stdout.write('Gemini key configured: ' + str(bool(settings.GEMINI_API_KEY)))
        self.stdout.write('Gemini chat model: ' + settings.GEMINI_MODEL)
        self.stdout.write('Gemini vision model: ' + settings.GEMINI_VISION_MODEL)
        self.stdout.write('Try-on key configured: ' + str(bool(settings.FASHN_API_KEY)))
        self.stdout.write('Shipping mode: ' + settings.SHIPPING_MODE)
        if options['live']:
            from shop.llm_graph import make_model, provider_error
            from shop.agents import AgentError
            try:
                response = make_model('gemini').invoke('Reply with only OK.')
                if not response.content:
                    raise CommandError('Provider returned an empty response.')
                self.stdout.write(self.style.SUCCESS('Gemini live response received. Tool/vision calls are separate checks.'))
            except AgentError as exc:
                raise CommandError(str(exc)) from None
            except Exception as exc:
                raise CommandError(provider_error(exc)) from None
