import os


class SecretString:
    def __init__(self, value: str):
        self._value: str = value

    def __repr__(self):
        return "********"

    def __str__(self):
        return "********"

    def reveal(self) -> str:
        return self._value


def get_required_env_var(env_var: str) -> SecretString:
    value = os.environ.get(env_var)
    if value is None:
        raise ValueError(f"{env_var} is a required environment variable that is missing.")
    return SecretString(value)
