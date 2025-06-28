from pennyspy.scrapers.rbc_bank.request_options import SoftwareExtension, Software


def get_default_filename(software: Software) -> str:
    return f"transaction_history{SoftwareExtension[software.name].value}"