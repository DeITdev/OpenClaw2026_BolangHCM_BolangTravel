"""All Google Maps / Google Search selectors live here.

Google ships obfuscated class names (Nv2PK, hfpxzc, ...) that change without
notice, so we prefer role / aria-label / text-based locators. Each entry
documents fallback strategies so it's easy to swap when Google's HTML moves.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MapsSearchSelectors:
    results_container: str = 'div[role="feed"]'
    result_link: str = 'a.hfpxzc'
    result_card: str = 'div[role="article"]'

    name_attr_from_link: str = "aria-label"

    rating_in_card: str = 'span.MW4etd'
    review_count_in_card: str = 'span.UY7F9'
    address_chip: str = 'div.W4Efsd > span > span'
    price_indicator: str = 'span.QrUbjf'

    no_results_marker: str = 'div.section-no-result'


@dataclass(frozen=True)
class MapsPlaceSelectors:
    title: str = 'h1.DUwDvf'
    rating: str = 'div.F7nice span[aria-hidden="true"]'
    review_count: str = 'div.F7nice span[aria-label*="review" i], div.F7nice span[aria-label*="ulasan" i]'

    info_buttons: str = 'button[data-item-id]'

    address_button: str = 'button[data-item-id="address"]'
    address_text: str = 'button[data-item-id="address"] div.Io6YTe'

    website_button: str = 'a[data-item-id="authority"]'
    phone_button: str = 'button[data-item-id^="phone"]'
    phone_text: str = 'button[data-item-id^="phone"] div.Io6YTe'

    hours_summary_button: str = 'div[aria-label*="Hours" i], div[aria-label*="Jam" i]'
    hours_table: str = 'table.eK4R0e'
    hours_table_row: str = 'table.eK4R0e tr'

    category_chip: str = 'button[jsaction*="category"]'


@dataclass(frozen=True)
class MapsDirectionsSelectors:
    trip_card: str = 'div[data-trip-index]'
    duration_in_card: str = 'div.Fk3sm'
    distance_in_card: str = 'div.ivN21e div'

    primary_route_summary: str = 'div[data-trip-index="0"]'


@dataclass(frozen=True)
class GoogleSearchSelectors:
    result_block: str = 'div.MjjYud'
    result_title: str = 'h3'
    result_link: str = 'a[href]'
    result_snippet: str = 'div.VwiC3b, div[data-content-feature="1"]'

    consent_accept_button: str = 'button[aria-label*="Accept" i], button[aria-label*="Setuju" i]'


MAPS_SEARCH = MapsSearchSelectors()
MAPS_PLACE = MapsPlaceSelectors()
MAPS_DIRECTIONS = MapsDirectionsSelectors()
GOOGLE_SEARCH = GoogleSearchSelectors()
