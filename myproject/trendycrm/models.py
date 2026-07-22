from django.db import models
from django.conf import settings
from django.utils import timezone


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

    def __str__(self):
        return f"Conversation #{self.pk}"

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
    conversation = models.ForeignKey(CRMConversation, on_delete=models.CASCADE, related_name='messages')
    sender = models.CharField(max_length=100)
    body = models.TextField()
    is_outbound = models.BooleanField(default=False)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='sent')
    created_at = models.DateTimeField(default=timezone.now)

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
