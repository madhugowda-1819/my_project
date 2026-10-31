import re
from django.core.exceptions import ValidationError


class SportMatePasswordValidator:
    def validate(self, password, user=None):
        if len(password) > 16:
            raise ValidationError('Password must not exceed 16 characters.')
        checks = (
            (r'[A-Z]', 'Password must include at least one uppercase letter.'),
            (r'[a-z]', 'Password must include at least one lowercase letter.'),
            (r'[0-9]', 'Password must include at least one number.'),
            (r'[^A-Za-z0-9]', 'Password must include at least one special character.'),
        )
        for pattern, message in checks:
            if not re.search(pattern, password):
                raise ValidationError(message)

    def get_help_text(self):
        return 'Password must be 8–16 characters and include uppercase, lowercase, number, and special character.'
