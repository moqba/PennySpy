from pennyspy.scrapers.get_required_env_var import get_required_env_var

USERNAME = get_required_env_var("FETCHER_USER")
PASSWORD = get_required_env_var("FETCHER_PASSWORD")