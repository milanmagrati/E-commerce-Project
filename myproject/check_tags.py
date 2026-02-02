
import re

def scan_tags(path):
    with open(path, 'r') as f:
        lines = f.readlines()
    
    stack = []
    
    # Regex to find tags
    tag_re = re.compile(r'{%\s*(\w+)')
    
    for i, line in enumerate(lines):
        matches = tag_re.findall(line)
        for tag in matches:
            if tag in ['if', 'for', 'block', 'with']:
                stack.append((tag, i + 1))
            elif tag in ['endif', 'endfor', 'endblock', 'endwith']:
                if not stack:
                    print(f"ERROR: Unmatched {tag} at line {i+1}")
                    continue
                
                last_tag, last_line = stack.pop()
                expected_end = 'end' + last_tag
                if tag != expected_end:
                    print(f"ERROR: Mismatched tag at line {i+1}. Found {tag}, expected {expected_end} (opened at {last_line})")
                    # Try to recover? 
                    # If we found endif but expected endfor, maybe we forgot endfor?
                    # Put the last tag back if we think this closing tag belongs to an earlier parent?
                    # For simple check, just report.

    if stack:
        print("Unclosed tags remaining:")
        for tag, line in stack:
            print(f"{tag} at line {line}")

if __name__ == '__main__':
    scan_tags('/home/milan-magrati/Desktop/EcommerceAdmin/myproject/templates/base.html')
