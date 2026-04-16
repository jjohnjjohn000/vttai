import re

ACTION_PATTERN = re.compile(
    r'(?:\*\*)?(?:\[\s*ACTION\s*\])?(?:\*\*)?\s*'
    r'(?:(?:[\*\-]\s*)?(?:Type|Action|Type d\'action)\s*:\s*(?P<type>[^\n]*)\s*)'
    r'(?:(?:[\*\-]\s*)?Intention\s*:\s*(?P<intention>.*?)\s*)?'
    r'(?:(?:[\*\-]\s*)?R[eéè]gle\s*(?:5e)?\s*:\s*(?P<regle>.*?)\s*)?'
    r'(?:(?:[\*\-]\s*)?Cible(?:s)?\s*:\s*(?P<cible>.*?))?(?=\n\s*\n|\[ACTION\]|</thought>|</think>|$)',
    re.IGNORECASE | re.DOTALL
)

with open("/tmp/text_log.txt", "r", encoding="utf-8") as f:
    content = f.read()

matches = list(ACTION_PATTERN.finditer(content))
for i, m in enumerate(matches):
    print(f"MATCH {i+1}:")
    print(m.groupdict())
