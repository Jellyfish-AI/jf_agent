import re


class NameRedactor:
    def __init__(self, preserve_names=None):
        self.redacted_names = {}
        self.seq = 0
        self.preserve_names = preserve_names or []

    def redact_name(self, name):
        if name in self.preserve_names:
            return name

        redacted_name = self.redacted_names.get(name)
        if not redacted_name:
            redacted_name = f'redacted-{self.seq:04}'
            self.seq += 1
            self.redacted_names[name] = redacted_name
        return redacted_name


JIRA_KEY_REGEX = re.compile(r'([a-z0-9]+)[-|_|/| ]?(\d+)', re.IGNORECASE)


def sanitize_text(text, strip_text_content):
    if not text or not strip_text_content:
        return text

    return (' ').join(
        {f'{m[0].upper().strip()}-{m[1].upper().strip()}' for m in JIRA_KEY_REGEX.findall(text)}
    )
