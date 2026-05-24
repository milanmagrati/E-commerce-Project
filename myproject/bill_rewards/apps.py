from django.apps import AppConfig


class BillRewardsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'bill_rewards'
    verbose_name = 'Bill OCR & Rewards'

    def ready(self):
        pass
