import os

def get_required_env_var(env_var: str) -> str:
    value = os.environ.get(env_var)
    if value is None:
        raise ValueError(f"{env_var} is a required environment variable that is missing.")
    return value
