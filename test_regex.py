import re

feats = ["squire of solamnia", "alert", "actor"]
pattern = r'\b(' + '|'.join(map(re.escape, sorted(feats, key=len, reverse=True))) + r')\b'
regex = re.compile(pattern, re.IGNORECASE)

text = "You gain the Squire of Solamnia feat. Also Actor and Alert."

for match in regex.finditer(text):
    print("Match:", match.group(0))

