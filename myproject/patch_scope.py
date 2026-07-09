import os

with open('trendycrm/views.py', 'r') as f:
    content = f.read()

old_scope = '"scope": "pages_show_list,pages_manage_metadata,pages_messaging,pages_read_engagement,pages_read_user_content,instagram_basic,instagram_manage_messages"'
new_scope = '"scope": "pages_show_list,pages_manage_metadata,pages_messaging,pages_read_engagement,pages_read_user_content,pages_manage_engagement,instagram_basic,instagram_manage_messages"'

if old_scope in content:
    content = content.replace(old_scope, new_scope)
    with open('trendycrm/views.py', 'w') as f:
        f.write(content)
    print("Scope updated successfully!")
else:
    print("Old scope not found. Let's check what's actually there.")
    import re
    match = re.search(r'"scope":\s*"(.*?)"', content)
    if match:
        print("Found scope:", match.group(1))
