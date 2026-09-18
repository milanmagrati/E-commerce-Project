"""
What Sentinel watches, and what it calls things.

Tracking is opt-out rather than opt-in: every model in the business apps is
watched unless it matches an exclusion. On a codebase with ~45 models in
dashboard alone (and more added regularly), an opt-in list would silently rot
the moment someone adds a model and forgets to register it — which is exactly
the change you most want in an audit trail.
"""

import re

# Apps whose models are business records worth auditing.
TRACKED_APPS = {
    'dashboard', 'accounts', 'hrm', 'ncm', 'pick_and_drop',
    'store', 'inventory', 'todo', 'chat', 'resources',
    'trendycrm', 'bill_rewards', 'google_sheets', 'integrations',
}

# Never tracked — Sentinel's own tables (infinite recursion), Django plumbing,
# and high-volume machine-written logs that would drown the human signal.
EXCLUDED_APPS = {'sentinel', 'sessions', 'admin', 'contenttypes', 'auth', 'authtoken'}

EXCLUDED_MODELS = {
    # Existing per-domain log tables — already visible in their own screens.
    'dashboard.OrderActivityLog',
    'dashboard.ReturnActivityLog',
    'dashboard.MaintenanceLog',
    'hrm.HRMAuditLog',
    'hrm.PayslipAuditLog',
    'hrm.AttendanceFixLog',
    'ncm.WebhookLog',
    'ncm.NCMBulkLogDetail',
    'ncm.NCMBulkLogOrder',
    'pick_and_drop.WebhookLog',
    # Machine-generated at high frequency — biometric punches arrive every few
    # seconds per device and carry no human decision.
    'hrm.BiometricAttendance',
    'trendycrm.CRMCreditLog',
    'trendycrm.CRMMessage',
    'trendycrm.CRMSocialComment',
    # Ephemeral storefront state.
    'store.Cart',
    'store.CartItem',
    'dashboard.FollowUpPresence',
}

# Model-name suffixes that are almost always machine chatter.
EXCLUDED_NAME_PATTERN = re.compile(r'(Log|Logs|Session|Token|Cache|Queue)$')

# Fields whose changes are noise: touched on every save, or huge blobs.
# NOTE: 'password' is deliberately NOT ignored — a credential change is one of the
# most important things an audit trail can show. It is stored redacted (see
# utils.redact_value), so the event proves the password changed without revealing it.
IGNORED_FIELDS = {
    'updated_at', 'modified_at', 'last_modified', 'last_activity', 'last_seen',
    'last_login', 'last_synced_at', 'sync_attempted_at', 'date_updated',
    'session_key', 'search_vector',
}

# Human-readable area names for the module column.
MODULE_LABELS = {
    'dashboard': 'Operations',
    'accounts': 'Access Control',
    'hrm': 'HR & Payroll',
    'ncm': 'NCM Logistics',
    'pick_and_drop': 'Pick & Drop',
    'store': 'Storefront',
    'inventory': 'Inventory',
    'todo': 'Tickets',
    'chat': 'Messaging',
    'resources': 'Knowledge Base',
    'trendycrm': 'Trendy CRM',
    'bill_rewards': 'Bill Rewards',
    'google_sheets': 'Sheet Imports',
    'integrations': 'Integrations',
    'sentinel': 'Sentinel Vault',
}

# Models that carry enough weight that touching them is inherently interesting.
# Feeds the risk score — editing a payroll run is not the same as editing a note.
HIGH_VALUE_MODELS = {
    'accounts.CustomUser': 30,
    'accounts.Role': 30,
    'hrm.PayrollRun': 25,
    'hrm.Payslip': 25,
    'hrm.EmployeeSalary': 30,
    'dashboard.CompanySetup': 20,
    'dashboard.Product': 10,
    'dashboard.Order': 10,
    'dashboard.Purchase': 15,
    'dashboard.Supplier': 10,
    'dashboard.APISettings': 30,
    'dashboard.LogisticsAPIConfig': 25,
    'trendycrm.CRMIntegration': 25,
}

# Field names that represent money or permission — changing them scores higher.
SENSITIVE_FIELD_PATTERN = re.compile(
    r'^(can_|is_superuser|is_staff|role|max_discount|.*_price$|.*_amount$|'
    r'.*salary.*|.*_secret$|.*_key$|commission.*|balance.*)',
    re.I,
)


def model_key(model):
    return f'{model._meta.app_label}.{model.__name__}'


def is_tracked(model):
    """Should writes to this model produce audit events?"""
    app_label = model._meta.app_label
    if app_label in EXCLUDED_APPS or app_label not in TRACKED_APPS:
        return False
    key = model_key(model)
    if key in EXCLUDED_MODELS:
        return False
    if EXCLUDED_NAME_PATTERN.search(model.__name__):
        return False
    # Auto-created M2M through-tables carry no independent meaning.
    if model._meta.auto_created:
        return False
    return True


def module_label(app_label):
    return MODULE_LABELS.get(app_label, (app_label or 'system').replace('_', ' ').title())


def object_weight(key):
    return HIGH_VALUE_MODELS.get(key, 0)


def is_sensitive_field(name):
    return bool(SENSITIVE_FIELD_PATTERN.match(name or ''))


def humanise_model(model):
    """'ProductVariation' -> 'Product Variation', respecting a model's own verbose_name."""
    verbose = getattr(model._meta, 'verbose_name', None)
    if verbose:
        return str(verbose).title()
    return re.sub(r'(?<!^)(?=[A-Z])', ' ', model.__name__)
