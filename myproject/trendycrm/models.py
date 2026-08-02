from datetime import timedelta

from decouple import config as env_config
from django.db import models
from django.conf import settings
from django.utils import timezone

# How long the AI stays quiet on a conversation after a staff member replies to
# it. Prevents the bot from talking over a human who has taken the chat over.
# Kept short by default: an operator who types one line and walks away shouldn't
# find the bot mute for half an hour. Use the per-chat AI toggle for a hard stop.
# Lives here rather than in meta_sync because both the auto-reply gate and the
# inbox UI read it — they must agree on when the bot resumes.
HUMAN_TAKEOVER_MINUTES = env_config('CRM_HUMAN_TAKEOVER_MINUTES', default=5, cast=int)


class CRMContact(models.Model):
    LEAD_STATUS_CHOICES = [
        ('new', 'New'),
        ('contacted', 'Contacted'),
        ('qualified', 'Qualified'),
        ('converted', 'Converted'),
        ('lost', 'Lost'),
    ]
    name = models.CharField(max_length=200)
    email = models.EmailField(blank=True, null=True)
    phone = models.CharField(max_length=30, blank=True, null=True)
    company = models.CharField(max_length=200, blank=True, null=True)
    meta_id = models.CharField(max_length=255, blank=True, null=True)
    status = models.CharField(max_length=20, choices=LEAD_STATUS_CHOICES, default='new')
    notes = models.TextField(blank=True, null=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name

    class Meta:
        ordering = ['-created_at']

class CRMLabel(models.Model):
    name = models.CharField(max_length=50)
    color_hex = models.CharField(max_length=7, default='#7c3aed')
    
    def __str__(self):
        return self.name

    class Meta:
        ordering = ['name']


class CRMConversation(models.Model):
    CHANNEL_CHOICES = [
        ('whatsapp', 'WhatsApp'),
        ('instagram', 'Instagram'),
        ('facebook', 'Facebook'),
        ('tiktok', 'TikTok'),
        ('gmail', 'Gmail'),
        ('outlook', 'Outlook'),
        ('zoho_mail', 'Zoho Mail'),
        ('email', 'Email'),
        ('sms', 'SMS'),
        ('web', 'Web Chat'),
    ]
    STATUS_CHOICES = [
        ('open', 'Open'),
        ('resolved', 'Resolved'),
        ('pending', 'Pending'),
    ]
    contact = models.ForeignKey(CRMContact, on_delete=models.SET_NULL, null=True, blank=True, related_name='conversations')
    integration = models.ForeignKey('CRMIntegration', on_delete=models.SET_NULL, null=True, blank=True, related_name='conversations')
    account_id = models.CharField(max_length=255, blank=True, null=True)
    channel = models.CharField(max_length=20, choices=CHANNEL_CHOICES, default='web')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='open')
    subject = models.CharField(max_length=300, blank=True, null=True)
    assigned_to = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    labels = models.ManyToManyField(CRMLabel, blank=True, related_name='conversations')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(default=timezone.now)
    last_message = models.TextField(blank=True, null=True)
    is_read = models.BooleanField(default=True)
    # Per-chat kill switch for the AI. Lets an operator silence the bot on one
    # conversation without pausing it for the whole page.
    ai_enabled = models.BooleanField(default=True)
    # Stamped whenever a staff member sends from the composer. The auto-reply
    # gate uses it to stay quiet while a human is actively handling the chat.
    last_human_reply_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"Conversation #{self.pk}"

    def ai_status(self, integration=None):
        """
        Single source of truth for "will the AI answer the next inbound message?".

        `integration` overrides the conversation's own FK — the webhook path knows
        which account actually received the message and holds a freshly-loaded
        object, where self.integration may be stale or unset.

        Both the auto-reply gate (meta_sync.process_incoming_webhook_message) and
        the inbox header read this, so the badge can never claim "AI On" while the
        bot is actually sitting out — which it did before, because the takeover
        pause and the toggle were separate pieces of state with no shared read.

        Returns a dict:
            state      — 'on' | 'paused' | 'off' | 'unavailable'
            label      — short text for the badge
            reason     — full sentence explaining the state (tooltip / log line)
            resumes_in — seconds until the bot resumes by itself, else None
            resumable  — True when the operator can lift this state from the chat
        """
        def status(state, label, reason, resumes_at=None, resumable=False):
            # resumes_at is an absolute instant, not a countdown: it has to stay
            # byte-identical across polls or the message fragment would differ on
            # every fetch and the inbox would rebuild the thread every 3 seconds.
            return {
                'state': state, 'label': label, 'reason': reason,
                'resumes_at': resumes_at.isoformat() if resumes_at else None,
                'resumes_in': (
                    max(0, int((resumes_at - timezone.now()).total_seconds()))
                    if resumes_at else None
                ),
                'resumable': resumable,
            }

        if not self.ai_enabled:
            return status('off', 'AI Off',
                          'The AI is switched off for this conversation.', resumable=True)

        integration = integration or (self.integration if self.integration_id else None)
        chatbot = integration.chatbot_config if integration else None
        if not chatbot:
            return status('unavailable', 'No AI',
                          'No chatbot is assigned to the channel this chat came from.')
        if not chatbot.is_active:
            return status('unavailable', 'AI Paused',
                          f"The '{chatbot.name}' chatbot is inactive.")
        if not (chatbot.auto_reply_channels or {}).get(str(integration.pk), False):
            return status('unavailable', 'AI Off',
                          'Auto-reply is not enabled for this channel on the Chatbot page.')
        if chatbot.ai_credits is not None and chatbot.ai_credits <= 0:
            return status('unavailable', 'No Credits',
                          'This business has no AI credits left.')

        if self.status == 'resolved':
            return status('paused', 'AI Idle',
                          'This conversation is resolved — the AI stays quiet until it is reopened.')
        if self.assigned_to_id:
            name = self.assigned_to.get_full_name() or self.assigned_to.username
            # Resumable: handing the chat back to the bot releases the claim too,
            # which is the only way out of a takeover that never expires.
            return status('paused', 'Human',
                          f'{name} is handling this conversation, so the AI stays quiet.',
                          resumable=True)

        if self.last_human_reply_at:
            resumes_at = self.last_human_reply_at + timedelta(minutes=HUMAN_TAKEOVER_MINUTES)
            if timezone.now() < resumes_at:
                return status('paused', 'Human',
                              'A team member just replied — the AI is paused so it cannot talk '
                              'over them.', resumes_at=resumes_at, resumable=True)

        return status('on', 'AI On', 'The AI will reply to the next customer message.')

    class Meta:
        ordering = ['-updated_at']


class CRMNote(models.Model):
    conversation = models.ForeignKey(CRMConversation, on_delete=models.CASCADE, related_name='notes')
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    text = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Note on {self.conversation} by {self.author}"

    class Meta:
        ordering = ['-created_at']


class CRMMessage(models.Model):
    STATUS_CHOICES = [
        ('sent', 'Sent'),
        ('delivered', 'Delivered'),
        ('read', 'Read'),
        ('failed', 'Failed'),
    ]
    ATTACHMENT_TYPE_CHOICES = [
        ('image', 'Image'),
        ('audio', 'Audio'),
        ('document', 'Document'),
    ]
    conversation = models.ForeignKey(CRMConversation, on_delete=models.CASCADE, related_name='messages')
    sender = models.CharField(max_length=100)
    body = models.TextField(blank=True)
    is_outbound = models.BooleanField(default=False)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='sent')
    attachment = models.FileField(upload_to='crm_attachments/%Y/%m/', blank=True, null=True)
    attachment_type = models.CharField(max_length=20, choices=ATTACHMENT_TYPE_CHOICES, blank=True, default='')
    attachment_name = models.CharField(max_length=255, blank=True, default='')
    created_at = models.DateTimeField(default=timezone.now)
    # Provider-side message id (Graph `id` / webhook `mid`). The old dedupe
    # matched on body + a ±1 minute window, which both missed repeats and
    # collided on short repeated messages ("ok"). Empty for locally-composed
    # messages that have no provider id yet.
    external_id = models.CharField(max_length=255, blank=True, default='', db_index=True)
    # True when the AI router generated this reply, as opposed to a staff member.
    is_ai = models.BooleanField(default=False)
    # Internal timeline events — "agent took over", "the AI couldn't reply". Shown
    # as a centered chip in the thread and NEVER sent to the customer, so that a
    # silent bot has a visible reason instead of looking like nothing happened.
    is_system = models.BooleanField(default=False)
    SYSTEM_LEVEL_CHOICES = [('info', 'Info'), ('warning', 'Warning')]
    system_level = models.CharField(max_length=10, choices=SYSTEM_LEVEL_CHOICES,
                                    blank=True, default='')
    # What the router classified the customer's message as, kept on the reply it
    # produced so the thread can show *why* the bot answered the way it did.
    ai_intent = models.CharField(max_length=40, blank=True, default='')

    # Intent → (chip label, priority). Priority is derived rather than stored:
    # it's a presentation of the intent, and two columns that must agree would
    # eventually disagree.
    INTENT_DISPLAY = {
        'purchase_intent': ('Purchase', 'high'),
        'product_issue': ('Issue', 'high'),
        'general_query': ('Query', 'low'),
        'spam_noise': ('Spam', 'low'),
    }

    @property
    def intent_label(self):
        return self.INTENT_DISPLAY.get(self.ai_intent, (self.ai_intent.replace('_', ' ').title(), 'low'))[0]

    @property
    def intent_priority(self):
        return self.INTENT_DISPLAY.get(self.ai_intent, ('', 'low'))[1]

    @classmethod
    def log_event(cls, conversation, text, level='info'):
        """Drops an internal event chip into a conversation's timeline."""
        return cls.objects.create(
            conversation=conversation, sender='System', body=text,
            is_outbound=False, is_system=True, system_level=level, status='sent',
        )

    @property
    def display_date(self):
        now = timezone.now().date()
        if timezone.is_aware(self.created_at):
            msg_date = timezone.localtime(self.created_at).date()
        else:
            msg_date = self.created_at.date()
            
        delta = now - msg_date
        if delta.days == 0:
            return "Today"
        elif delta.days == 1:
            return "Yesterday"
        else:
            return msg_date.strftime("%b %d, %Y")

    class Meta:
        ordering = ['created_at']


class CRMQuickReply(models.Model):
    name = models.CharField(max_length=100)
    shortcut = models.CharField(max_length=50, unique=True)
    content = models.TextField()
    category = models.CharField(max_length=100, blank=True, null=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

    class Meta:
        ordering = ['name']


class CRMChatbotConfig(models.Model):
    AI_MODEL_CHOICES = [
        ('multi_auto', 'Multi-Model Auto (Recommended)'),
        ('gpt-4o', 'GPT-4o (Premium)'),
        ('gpt-3.5-turbo', 'GPT-3.5 Turbo (Fast)'),
        ('gemini-flash-latest', 'Gemini Flash (Cheapest)'),
        ('gemini-pro-latest', 'Gemini Pro'),
    ]
    # ── Purpose-based provider routing ────────────────────────────────────────
    # Lets the operator pick which AI provider handles each purpose independently
    # (e.g. OpenAI for text replies, Gemini for image recognition). 'auto' lets the
    # router choose the best available provider and fall back gracefully.
    PROVIDER_CHOICES = [
        ('auto', 'Auto — Smart Routing (Recommended)'),
        ('openai', 'OpenAI (GPT)'),
        ('gemini', 'Google Gemini'),
    ]
    # Suggested model names surfaced in the UI. The field accepts any string so
    # newer models (e.g. a future gpt-5) can be typed in without a code change.
    OPENAI_MODEL_CHOICES = [
        ('gpt-4o', 'GPT-4o'),
        ('gpt-4o-mini', 'GPT-4o Mini (Cheaper)'),
        ('gpt-4-turbo', 'GPT-4 Turbo'),
        ('gpt-4.1', 'GPT-4.1'),
        ('gpt-5', 'GPT-5'),
        ('gpt-3.5-turbo', 'GPT-3.5 Turbo (Fastest)'),
    ]
    GEMINI_MODEL_CHOICES = [
        ('gemini-flash-latest', 'Gemini Flash (Cheapest)'),
        ('gemini-flash-lite-latest', 'Gemini Flash Lite (Fastest)'),
        ('gemini-pro-latest', 'Gemini Pro (Premium)'),
    ]
    TONE_CHOICES = [
        ('professional_friendly', 'Professional & Friendly'),
        ('formal', 'Formal'),
        ('casual', 'Casual'),
        ('energetic', 'Energetic & Hype'),
        ('empathetic', 'Empathetic & Supportive'),
    ]
    name = models.CharField(max_length=200, default='Trendy AI')
    is_active = models.BooleanField(default=False)
    # Business Identity
    business_name = models.CharField(max_length=200, blank=True, null=True)
    business_email = models.EmailField(blank=True, null=True)
    business_phone = models.CharField(max_length=30, blank=True, null=True)
    about_blurb = models.TextField(blank=True, null=True, help_text='About your business for the AI')
    welcome_message = models.TextField(blank=True, null=True)
    business_address = models.TextField(blank=True, null=True, help_text='Shop/office address the AI can give out')
    # Delivery/service coverage, e.g. ["Kathmandu", "Lalitpur", "Pokhara"]. A list
    # rather than free text so the AI can answer "do you deliver to X?" precisely.
    cities_served = models.JSONField(default=list, blank=True)
    social_facebook = models.CharField(max_length=255, blank=True, null=True)
    social_instagram = models.CharField(max_length=255, blank=True, null=True)
    social_tiktok = models.CharField(max_length=255, blank=True, null=True)
    social_whatsapp = models.CharField(max_length=255, blank=True, null=True)
    # Business Knowledge Sections
    tone_voice = models.TextField(blank=True, null=True, help_text='How should the AI speak? Describe tone, style, brand voice.')
    offerings = models.TextField(blank=True, null=True, help_text='Products and services you sell.')
    faq_text = models.TextField(blank=True, null=True, help_text='Common FAQs and their answers.')
    playbook = models.TextField(blank=True, null=True, help_text='How to handle tricky situations, complaints, refunds.')
    # Agent Configuration
    ai_model = models.CharField(max_length=50, choices=AI_MODEL_CHOICES, default='multi_auto')
    response_tone = models.CharField(max_length=30, choices=TONE_CHOICES, default='professional_friendly')
    
    # Advanced AI Logic
    CREATIVITY_CHOICES = [
        ('0.3', 'Strict & Factual'),
        ('0.7', 'Balanced'),
        ('1.2', 'Creative & Marketing'),
    ]
    LENGTH_CHOICES = [
        ('100', 'Short & Punchy'),
        ('500', 'Standard'),
        ('1500', 'Detailed & Explanatory'),
    ]
    LANGUAGE_CHOICES = [
        ('auto', 'Auto-Detect'),
        ('en', 'English'),
        ('es', 'Spanish'),
        ('fr', 'French'),
        ('ne', 'Nepali'),
        ('hi', 'Hindi'),
    ]
    creativity_level = models.CharField(max_length=10, choices=CREATIVITY_CHOICES, default='0.7')
    response_length = models.CharField(max_length=10, choices=LENGTH_CHOICES, default='500')
    primary_language = models.CharField(max_length=10, choices=LANGUAGE_CHOICES, default='auto')
    
    # Handoff keywords
    handoff_triggers = models.JSONField(default=list, blank=True)

    auto_reply_channels = models.JSONField(default=dict)
    ai_credits = models.IntegerField(default=100)
    # Multi-Model Routing Engine API Keys
    openai_api_key = models.CharField(max_length=500, blank=True, null=True, help_text='OpenAI API key for ChatGPT Premium (complex text, images)')
    gemini_api_key = models.CharField(max_length=500, blank=True, null=True, help_text='Google Gemini API key (audio, simple FAQ via Flash)')

    # Purpose-based provider selection (see PROVIDER_CHOICES above)
    text_provider = models.CharField(
        max_length=20, choices=PROVIDER_CHOICES, default='auto',
        help_text='Which provider generates text/chat replies.'
    )
    image_provider = models.CharField(
        max_length=20, choices=PROVIDER_CHOICES, default='auto',
        help_text='Which provider handles image recognition (OCR / vision).'
    )
    openai_model = models.CharField(
        max_length=60, default='gpt-4o', blank=True,
        help_text='OpenAI model name to use when OpenAI handles a request.'
    )
    gemini_model = models.CharField(
        max_length=60, default='gemini-flash-latest', blank=True,
        help_text='Gemini model name to use when Gemini handles a request.'
    )

    updated_at = models.DateTimeField(auto_now=True)

    # Why the last auto-reply attempt produced nothing (rate limit, missing key,
    # provider error). Without this the bot just goes quiet and there's no way to
    # tell a configuration problem from "nobody has messaged us".
    last_error = models.TextField(blank=True, default='')
    last_error_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = 'Chatbot Configuration'


class CRMCreditLog(models.Model):
    """Tracks AI credit usage for the credit history modal."""
    chatbot_config = models.ForeignKey(CRMChatbotConfig, on_delete=models.CASCADE, related_name='credit_logs', null=True)
    ACTION_CHOICES = [
        ('auto_reply', 'Auto Reply'),
        ('intent_classify', 'Intent Classification'),
        ('comment_dm', 'Comment-to-DM'),
        ('manual_test', 'Manual Test'),
        ('top_up', 'Top Up'),
    ]
    action = models.CharField(max_length=30, choices=ACTION_CHOICES)
    credits_used = models.IntegerField(default=1)  # positive = top-up, negative = usage
    model_used = models.CharField(max_length=50, blank=True, null=True)
    description = models.CharField(max_length=300, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Credit Log'



class CRMPageProfile(models.Model):
    """
    Centralized Knowledge Core for each social page/channel.
    Maps each CRMIntegration (page) to its unique product, price, tone, and checkout link.
    This drives the Dynamic Prompt Assembly — when a webhook arrives from a page, the AI
    automatically fetches the correct profile and builds a tailored response.
    """
    TONE_CHOICES = [
        ('friendly', 'Friendly & Casual'),
        ('professional', 'Professional & Formal'),
        ('energetic', 'Energetic & Hype'),
        ('empathetic', 'Empathetic & Supportive'),
        ('luxury', 'Luxury & Exclusive'),
    ]
    integration = models.OneToOneField(
        'CRMIntegration', on_delete=models.CASCADE,
        related_name='page_profile',
        help_text='The connected social channel this profile belongs to'
    )
    product_name = models.CharField(max_length=300, blank=True, null=True, help_text='Main product name sold on this page')
    price = models.CharField(max_length=100, blank=True, null=True, help_text='Product price (e.g. NPR 1,200)')
    brand_tone = models.CharField(max_length=20, choices=TONE_CHOICES, default='friendly')
    checkout_link = models.URLField(max_length=1000, blank=True, null=True, help_text='Direct checkout / buy link sent in DMs')
    custom_faq = models.TextField(blank=True, null=True, help_text='Custom FAQs or product description for the AI knowledge base')
    # Comment-to-DM automation settings
    comment_auto_reply_enabled = models.BooleanField(default=False, help_text='Auto-reply publicly to comments and DM the sender')
    public_reply_template = models.TextField(
        blank=True, null=True,
        default='Just sent the link to your DMs! 💌',
        help_text='The public comment reply text (The Hook)'
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Profile: {self.integration}"

    class Meta:
        verbose_name = 'Page Profile'
        ordering = ['integration__channel_type']


class CRMIntegration(models.Model):
    CHANNEL_TYPE_CHOICES = [
        ('whatsapp', 'WhatsApp'),
        ('instagram', 'Instagram'),
        ('facebook', 'Facebook Messenger'),
        ('tiktok', 'TikTok'),
        ('gmail', 'Gmail'),
        ('outlook', 'Outlook'),
        ('zoho_mail', 'Zoho Mail'),
    ]
    STATUS_CHOICES = [
        ('connected', 'Connected'),
        ('not_connected', 'Not Connected'),
        ('error', 'Error'),
    ]
    channel_type = models.CharField(max_length=30, choices=CHANNEL_TYPE_CHOICES)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='not_connected')
    account_name = models.CharField(max_length=200, blank=True, null=True)
    access_token = models.TextField(blank=True, null=True)
    connected_at = models.DateTimeField(null=True, blank=True)
    meta = models.JSONField(default=dict)
    chatbot_config = models.ForeignKey(
        'CRMChatbotConfig', 
        on_delete=models.SET_NULL, 
        null=True, blank=True, 
        related_name='integrations'
    )

    @property
    def parsed_name(self):
        import re
        if self.account_name:
            match = re.match(r'^(.*?) \((\d+)\)$', self.account_name.strip())
            if match:
                return match.group(1).strip()
        return self.account_name

    @property
    def parsed_id(self):
        import re
        if self.account_name:
            match = re.match(r'^(.*?) \((\d+)\)$', self.account_name.strip())
            if match:
                return match.group(2)
        return ''

    def __str__(self):
        return f"{self.get_channel_type_display()} - {self.status}"

    class Meta:
        ordering = ['channel_type']


class CRMTicket(models.Model):
    PRIORITY_CHOICES = [
        ('low', 'Low'),
        ('medium', 'Medium'),
        ('high', 'High'),
        ('urgent', 'Urgent'),
    ]
    STATUS_CHOICES = [
        ('open', 'Open'),
        ('in_progress', 'In Progress'),
        ('resolved', 'Resolved'),
        ('closed', 'Closed'),
    ]
    title = models.CharField(max_length=300)
    description = models.TextField(blank=True, null=True)
    contact = models.ForeignKey(CRMContact, on_delete=models.SET_NULL, null=True, blank=True)
    priority = models.CharField(max_length=10, choices=PRIORITY_CHOICES, default='medium')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='open')
    assigned_to = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"#{self.pk} - {self.title}"

    class Meta:
        ordering = ['-created_at']


class CRMSocialPost(models.Model):
    integration = models.ForeignKey(CRMIntegration, on_delete=models.CASCADE, related_name='social_posts')
    meta_post_id = models.CharField(max_length=255, unique=True)
    message = models.TextField(blank=True, null=True)
    picture_url = models.URLField(max_length=1000, blank=True, null=True)
    created_time = models.DateTimeField(null=True, blank=True)
    likes_count = models.IntegerField(default=0)
    comments_count = models.IntegerField(default=0)
    is_starred = models.BooleanField(default=False)
    is_reviewed = models.BooleanField(default=False)
    is_read = models.BooleanField(default=True)
    reactions_data = models.JSONField(default=dict, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def latest_comment_snippet(self):
        if hasattr(self, 'annotated_latest_sender') and hasattr(self, 'annotated_latest_message'):
            if self.annotated_latest_sender:
                msg = self.annotated_latest_message or ""
                return f"{self.annotated_latest_sender} - {msg}"
            return ""
            
        latest = self.comments.exclude(sender_name__isnull=True).exclude(sender_name='').order_by('-created_time').first()
        if latest:
            msg = latest.message or ""
            return f"{latest.sender_name} - {msg}"
        return ""

    @property
    def latest_comment_time(self):
        if hasattr(self, 'annotated_latest_time') and self.annotated_latest_time:
            return self.annotated_latest_time
            
        latest = self.comments.exclude(sender_name__isnull=True).exclude(sender_name='').order_by('-created_time').first()
        if latest and latest.created_time:
            return latest.created_time
        return self.created_time

    def __str__(self):
        return f"Post {self.meta_post_id}"

    class Meta:
        ordering = ['-created_time']


class CRMSocialComment(models.Model):
    VISIBILITY_CHOICES = [
        ('visible', 'Visible'),
        ('hidden', 'Hidden'),
        ('spam', 'Spam'),
    ]
    WORKFLOW_CHOICES = [
        ('open', 'Open'),
        ('done', 'Done'),
    ]
    post = models.ForeignKey(CRMSocialPost, on_delete=models.CASCADE, related_name='comments')
    meta_comment_id = models.CharField(max_length=255, unique=True)
    parent_comment = models.ForeignKey('self', on_delete=models.CASCADE, null=True, blank=True, related_name='replies')
    sender_name = models.CharField(max_length=200)
    sender_id = models.CharField(max_length=255, blank=True, null=True)
    message = models.TextField()
    created_time = models.DateTimeField(null=True, blank=True)
    like_count = models.IntegerField(default=0)
    reactions_data = models.JSONField(default=dict, blank=True, null=True)
    assigned_to = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    workflow_status = models.CharField(max_length=20, choices=WORKFLOW_CHOICES, default='open')
    visibility_status = models.CharField(max_length=20, choices=VISIBILITY_CHOICES, default='visible')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Comment by {self.sender_name}"

    class Meta:
        ordering = ['-created_time']


class CommentAutomation(models.Model):
    """
    ManyChat-style comment automation rule.
    When a comment matches the trigger keyword on a connected page,
    the system auto-replies publicly and optionally sends a private DM.
    """
    MATCH_TYPE_CHOICES = [
        ('exact', 'Exact Match'),
        ('contains', 'Contains Keyword'),
        ('any', 'Any Comment'),
    ]

    integration = models.ForeignKey(
        CRMIntegration, on_delete=models.CASCADE,
        related_name='comment_automations',
        help_text='The social page this automation applies to'
    )
    name = models.CharField(max_length=200, help_text='Friendly name for this automation')
    trigger_keyword = models.CharField(
        max_length=200, blank=True,
        help_text='Keyword to match (leave blank if match_type=any)'
    )
    match_type = models.CharField(
        max_length=20, choices=MATCH_TYPE_CHOICES, default='contains'
    )
    public_reply = models.TextField(
        help_text='The public comment reply text shown on the post'
    )
    send_dm = models.BooleanField(
        default=False,
        help_text='Also send a private DM to the commenter'
    )
    dm_message = models.TextField(
        blank=True,
        help_text='The private DM body (can include links, product details, etc.)'
    )
    is_active = models.BooleanField(default=True)
    trigger_count = models.IntegerField(
        default=0,
        help_text='How many times this automation has fired'
    )
    last_triggered_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.name} [{self.integration}]"

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Comment Automation'
        verbose_name_plural = 'Comment Automations'
