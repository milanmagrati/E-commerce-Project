"""
Management command to setup NCM webhook registration
Usage: python manage.py setup_ncm_webhook --domain https://yourdomain.com --test
"""

from django.core.management.base import BaseCommand, CommandError
from django.conf import settings
from services.ncm_service import NCMService
import logging

logger = logging.getLogger('ncm')


class Command(BaseCommand):
    help = 'Register and test NCM webhook endpoint'

    def add_arguments(self, parser):
        parser.add_argument(
            '--domain',
            type=str,
            help='Domain for webhook URL (e.g., https://yourdomain.com)',
            default=None
        )
        parser.add_argument(
            '--test',
            action='store_true',
            help='Test webhook after registration',
            default=False
        )
        parser.add_argument(
            '--endpoint',
            type=str,
            help='Webhook endpoint path (default: /ncm/webhook/)',
            default='/ncm/webhook/'
        )

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS('═' * 70))
        self.stdout.write(self.style.SUCCESS('  🔗 NCM Webhook Setup & Registration'))
        self.stdout.write(self.style.SUCCESS('═' * 70))
        self.stdout.write('')

        domain = options.get('domain')
        endpoint = options.get('endpoint')
        test_webhook = options.get('test')

        # Step 1: Get domain
        if not domain:
            self.stdout.write(
                self.style.WARNING(
                    'ℹ️  No domain provided. Using localhost:8000'
                )
            )
            domain = 'http://127.0.0.1:8000'
        else:
            self.stdout.write(f'✅ Using domain: {domain}')

        webhook_url = f"{domain}{endpoint}"
        self.stdout.write(f'✅ Using endpoint: {endpoint}')
        self.stdout.write(f'📍 Full webhook URL: {self.style.SUCCESS(webhook_url)}')
        self.stdout.write('')

        # Verify domain format
        if not domain.startswith(('http://', 'https://')):
            raise CommandError(
                f'Invalid domain: {domain}\n'
                'Domain must start with http:// or https://'
            )

        # Step 2: Initialize NCM Service
        self.stdout.write('🔧 Initializing NCM Service...')
        try:
            service = NCMService()
            self.stdout.write(self.style.SUCCESS('✅ NCM Service initialized'))
        except Exception as e:
            raise CommandError(
                f'Failed to initialize NCM Service: {str(e)}\n'
                'Check your NCM API credentials in settings.py'
            )

        self.stdout.write('')

        # Step 3: Register webhook with NCM
        self.stdout.write('📤 Attempting to register webhook with NCM...')
        self.stdout.write('-' * 70)

        try:
            result = service.set_webhook_url(webhook_url)

            if result.get('success'):
                self.stdout.write(self.style.SUCCESS('✅ Webhook registered successfully!'))
                self.stdout.write('')
                self.stdout.write('📋 Response from NCM:')
                response_data = result.get('data', {})
                if isinstance(response_data, dict):
                    for key, value in response_data.items():
                        self.stdout.write(f'   • {key}: {value}')
                else:
                    self.stdout.write(f'   {response_data}')
            else:
                error = result.get('error', 'Unknown error')
                raise CommandError(
                    f'Failed to register webhook: {error}'
                )

        except CommandError:
            raise
        except Exception as e:
            raise CommandError(
                f'Exception during webhook registration: {str(e)}\n'
                'Check your internet connection and NCM API credentials'
            )

        self.stdout.write('')
        self.stdout.write('-' * 70)

        # Step 4: Optional - Test webhook
        if test_webhook:
            self.stdout.write('🧪 Testing webhook endpoint...')
            self.stdout.write('-' * 70)

            try:
                test_result = service.test_webhook(webhook_url)

                if test_result.get('success'):
                    self.stdout.write(self.style.SUCCESS('✅ Webhook test passed!'))
                    self.stdout.write('')
                    self.stdout.write('📋 Test Response:')
                    test_data = test_result.get('data', {})
                    if isinstance(test_data, dict):
                        for key, value in test_data.items():
                            self.stdout.write(f'   • {key}: {value}')
                    else:
                        self.stdout.write(f'   {test_data}')
                else:
                    error = test_result.get('error', 'Unknown error')
                    self.stdout.write(
                        self.style.WARNING(
                            f'⚠️  Webhook test failed: {error}'
                        )
                    )
                    self.stdout.write(
                        self.style.WARNING(
                            'This may indicate connectivity issues with NCM API'
                        )
                    )

            except Exception as e:
                self.stdout.write(
                    self.style.WARNING(
                        f'⚠️  Exception during webhook test: {str(e)}'
                    )
                )

            self.stdout.write('')
            self.stdout.write('-' * 70)

        # Step 5: Summary and next steps
        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('✅ Setup Complete!'))
        self.stdout.write('')
        self.stdout.write('📝 Summary:')
        self.stdout.write(f'   • Webhook URL: {self.style.SUCCESS(webhook_url)}')
        self.stdout.write(f'   • Status: Registered with NCM')
        if test_webhook:
            self.stdout.write(f'   • Test Status: Completed')
        else:
            self.stdout.write(f'   • Test Status: Skipped (use --test flag to run)')
        self.stdout.write('')
        self.stdout.write('🚀 Next Steps:')
        self.stdout.write('   1. Ensure your server is accessible at the domain')
        self.stdout.write('   2. Monitor webhook events in: /logs/ncm_integration.log')
        self.stdout.write('   3. Test by sending an order to NCM')
        self.stdout.write('   4. Check logs for incoming webhook notifications')
        self.stdout.write('')
        self.stdout.write('📚 Useful Commands:')
        self.stdout.write('   • View logs: tail -f logs/ncm_integration.log')
        self.stdout.write('   • Test webhook: python manage.py setup_ncm_webhook --test')
        self.stdout.write('   • Update webhook: python manage.py setup_ncm_webhook --domain https://newdomain.com')
        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('═' * 70))
