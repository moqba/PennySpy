from enum import StrEnum


class ActivityCss(StrEnum):
    """Selectors for the Base UI accordion the activity feed is built from.

    WS rebuilt the feed in September 2026. Rows no longer carry an ``id`` ending in
    ``-header``, and Base UI only links a panel with ``aria-controls`` once that panel has
    been opened, so a collapsed row exposes no id to address it by at all. What stayed true
    is the shape: every activity row is an accordion header button that leads with an icon
    span, while the Filters rail's accordions (Quick filters, Account, Type, Holdings,
    Timeframe, Status) lead with plain text. Everything else about the row -- its classes,
    its ids -- is a styled-components hash that WS regenerates on each front-end deploy.
    """

    # The page's main column. FullStory's hook outlives the styled-components hashes, and
    # scoping to it keeps the Filters rail out of every selector below.
    PAGE_ROOT = '[data-fullstory="page-activity"]'
    # The day a run of rows belongs to. The feed's only <h3>s; "Filters" is an <h2>.
    DATE_HEADER = '[data-fullstory="page-activity"] h3'
    # Every accordion header in the main column -- rows and filter panels alike. Pair it
    # with ROW_ICON to keep only the rows.
    ACCORDION_BUTTON = '[data-fullstory="page-activity"] button[aria-expanded]'
    # The leading icon span a row header always has and a filter panel never does. The depth
    # is what does the work: a filter panel's chevron is aria-hidden too, but sits directly
    # under the button rather than inside its content span.
    ROW_ICON = ':scope > span > span[aria-hidden="true"]'
    # A security's logo inside that icon span, labelled with the ticker. Its presence is what
    # separates a row titled with a ticker from one titled with a payee.
    ROW_TICKER_LOGO = ':scope > span > span[aria-hidden="true"] [role="img"][aria-label]'
    # The one node inside a row header WS still marks unmask: the status badge ("Cancelled").
    # The attribute used to mark every header field and now marks only this one, so it can no
    # longer be used to find the header's text -- only to tell the badge apart from it.
    ROW_STATUS = '[data-fs-privacy-rule="unmask"]'
