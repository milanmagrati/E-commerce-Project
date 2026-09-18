from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('trendycrm', '0023_crmcreditlog_chatbot_config'),
    ]

    operations = [
        migrations.AddField(
            model_name='crmchatbotconfig',
            name='text_provider',
            field=models.CharField(
                choices=[('auto', 'Auto — Smart Routing (Recommended)'), ('openai', 'OpenAI (GPT)'), ('gemini', 'Google Gemini')],
                default='auto', max_length=20,
                help_text='Which provider generates text/chat replies.',
            ),
        ),
        migrations.AddField(
            model_name='crmchatbotconfig',
            name='image_provider',
            field=models.CharField(
                choices=[('auto', 'Auto — Smart Routing (Recommended)'), ('openai', 'OpenAI (GPT)'), ('gemini', 'Google Gemini')],
                default='auto', max_length=20,
                help_text='Which provider handles image recognition (OCR / vision).',
            ),
        ),
        migrations.AddField(
            model_name='crmchatbotconfig',
            name='openai_model',
            field=models.CharField(
                blank=True, default='gpt-4o', max_length=60,
                help_text='OpenAI model name to use when OpenAI handles a request.',
            ),
        ),
        migrations.AddField(
            model_name='crmchatbotconfig',
            name='gemini_model',
            field=models.CharField(
                blank=True, default='gemini-flash-latest', max_length=60,
                help_text='Gemini model name to use when Gemini handles a request.',
            ),
        ),
    ]
