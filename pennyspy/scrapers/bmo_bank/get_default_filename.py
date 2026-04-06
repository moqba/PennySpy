from pennyspy.scrapers.bmo_bank.request_options import AppType, AppTypeExtension


def get_default_filename(app_type: AppType) -> str:
    return f"bmo_transactions{AppTypeExtension[app_type.name].value}"
