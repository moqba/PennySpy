# Project Instructions For AI Agents

## Scraper Logging And Errors

- Keep all scrapers verbose about browser actions and failure context.
- Before a required Selenium action or wait, log what the scraper is about to do, including the bank/flow step and timeout when applicable.
- When a required wait, click, submit, navigation, or field entry fails, raise an explicit contextual error that names the action, and preserve the original exception with `raise ... from e`.
- Do not log credentials, OTP codes, cookies, tokens, or other secrets. Use redacted action descriptions for sensitive inputs.
- Use the shared helpers on `Scraper` for Selenium navigation, waits, element lookup, clicks, submits, and key entry instead of adding bare Selenium calls in bank scrapers.
- Optional probes, such as cookie banners or trust-device checkboxes, should log that they were not found and continue without treating the timeout as an error.
- For critical browser-flow failures, save a screenshot when it helps diagnose what the page showed at the failure point.
