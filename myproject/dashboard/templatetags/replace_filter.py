from django import template

register = template.Library()

@register.filter
def replace(value, arg):
    """
    Replaces all occurrences of the first argument with the second in the string.
    Usage: {{ value|replace:"old,new" }}
    """
    try:
        old, new = arg.split(',')
        return str(value).replace(old, new)
    except ValueError:
        return value
