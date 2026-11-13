from pathlib import Path
from django.conf import settings
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Configure Gemini/local/OpenAI without displaying or overwriting keys."

    def add_arguments(self, parser):
        parser.add_argument('--provider', choices=['gemini', 'local', 'openai'], default='gemini')
        parser.add_argument('--model', default=None)

    def handle(self, *args, **options):
        path = Path(settings.BASE_DIR) / '.env'
        source = path.read_text() if path.exists() else path.with_name('.env.example').read_text()
        changes = {'AI_MODE': options['provider'], 'AGENT_ENGINE': 'langgraph'}
        if options['model']:
            key = 'GEMINI_MODEL' if options['provider'] == 'gemini' else 'OPENAI_MODEL'
            changes[key] = options['model']
        lines = source.splitlines()
        for key, value in changes.items():
            lines = [line for line in lines if not line.startswith(key + '=')]
            lines.append(key + '=' + value)
        path.write_text('\n'.join(lines) + '\n')
        self.stdout.write('AI settings saved. Restart Django. Keys were not displayed or overwritten.')
