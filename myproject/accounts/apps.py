from django.apps import AppConfig


class AccountsConfig(AppConfig):
    # accounts_customuser.id and every FK referencing it are int(11) in the
    # live MySQL DB. The project-wide DEFAULT_AUTO_FIELD is BigAutoField, so
    # without this override Django treats CustomUser's pk as BIGINT and emits
    # new FKs as BIGINT -> type mismatch -> MySQL Error 150 on deploy.
    default_auto_field = 'django.db.models.AutoField'
    name = 'accounts'
