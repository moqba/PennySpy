from pennyspy.scrapers.rbc_bank.request_options import Software, SoftwareExtension


def get_default_filename(software: Software) -> str:
    return f"transaction_history{SoftwareExtension[software.name].value}"
