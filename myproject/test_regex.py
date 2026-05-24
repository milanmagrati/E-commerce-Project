import re

full_text = """e Print	x Close	
Trendy Shopping	INVOICE	
Invoice #	T145	
Oate 19 May 2026"""

shop_name = ''
for line in full_text.split('\n'):
    line = line.strip()
    if not line: continue
    
    # Check if the line only contains skip words
    only_skip_words = re.sub(r'(?i)\b(e|print|close|x|invoice|receipt)\b|\s+', '', line)
    if not only_skip_words:
        continue
        
    # Remove those words to get the real name
    clean_line = re.sub(r'(?i)\b(e|print|close|x|invoice|receipt)\b', '', line).strip()
    if clean_line:
        shop_name = clean_line
        break

print(f"Shop: {shop_name}")

