import re

def analyze_template_structure(path):
    with open(path, 'r') as f:
        lines = f.readlines()
    
    stack = []
    tag_re = re.compile(r'{%\s*(\w+)')
    
    print("Template Tag Structure Analysis:")
    print("=" * 80)
    
    for i, line in enumerate(lines):
        matches = tag_re.findall(line)
        for tag in matches:
            indent = len(stack) * 2
            
            if tag in ['if', 'for', 'block', 'with']:
                print(f"{' ' * indent}Line {i+1}: OPEN {tag}")
                stack.append((tag, i + 1))
            elif tag in ['elif', 'else']:
                print(f"{' ' * indent}Line {i+1}: {tag.upper()}")
            elif tag in ['endif', 'endfor', 'endblock', 'endwith']:
                expected_tag = tag.replace('end', '')
                
                if not stack:
                    print(f"{' ' * indent}Line {i+1}: ERROR - Unmatched {tag}")
                    continue
                
                last_tag, last_line = stack[-1]
                
                if expected_tag == last_tag:
                    stack.pop()
                    print(f"{' ' * (indent-2)}Line {i+1}: CLOSE {tag} (opened at line {last_line})")
                else:
                    print(f"{' ' * indent}Line {i+1}: ERROR - Found {tag}, expected end{last_tag} (opened at line {last_line})")
                    print(f"{' ' * indent}         Stack: {stack}")
    
    print("\n" + "=" * 80)
    if stack:
        print("UNCLOSED TAGS:")
        for tag, line in stack:
            print(f"  - {tag} at line {line}")
    else:
        print("All tags properly closed!")

if __name__ == '__main__':
    analyze_template_structure('/home/milan-magrati/Desktop/EcommerceAdmin/myproject/templates/base.html')
