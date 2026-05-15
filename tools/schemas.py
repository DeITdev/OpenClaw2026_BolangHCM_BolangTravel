"""Pydantic models describing tool inputs and outputs.

LangChain serializes these to JSON-schema for the LLM's function-calling, so
clear field descriptions matter — the agent reads them to decide when to call
each tool.
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class SearchPlacesInput(BaseModel):
    query: str = Field(
        description=(
            "What kind of place to search for. Free-form text in Indonesian or "
            "English, e.g. 'wisata alam', 'kuliner malam', 'pantai keluarga'."
        )
    )
    city: str = Field(description="City or area name, e.g. 'Surabaya', 'Bandung Selatan'.")
    limit: int = Field(default=6, ge=1, le=10, description="Max number of places to return.")


class PlaceSummary(BaseModel):
    name: str
    address: Optional[str] = None
    rating: Optional[float] = None
    review_count: Optional[int] = None
    price_level: Optional[str] = None
    maps_url: Optional[str] = None


class SearchPlacesOutput(BaseModel):
    query: str
    city: str
    places: List[PlaceSummary]


class PlaceDetailsInput(BaseModel):
    name: str = Field(description="Exact name of the place (as returned by search_places_on_maps).")
    city: str = Field(description="City/area the place is in, to disambiguate same-name places.")


class OpeningHoursEntry(BaseModel):
    day: str
    hours: str


class PlaceDetails(BaseModel):
    name: str
    address: Optional[str] = None
    rating: Optional[float] = None
    review_count: Optional[int] = None
    category: Optional[str] = None
    phone: Optional[str] = None
    website: Optional[str] = None
    opening_hours: List[OpeningHoursEntry] = Field(default_factory=list)
    is_open_today: Optional[bool] = None
    maps_url: Optional[str] = None
    screenshot_path: Optional[str] = None


class DirectionsInput(BaseModel):
    origin: str = Field(description="Starting location, e.g. 'Surabaya Pusat' or a hotel name.")
    waypoints: List[str] = Field(
        description=(
            "Ordered list of stops between origin and destination. Use the place "
            "names from search results. The agent may pass them unordered; Google "
            "Maps does not auto-reorder, so list them in the order intended."
        )
    )


class TransportOption(BaseModel):
    duration_text: Optional[str] = None
    distance_text: Optional[str] = None
    available: bool = True
    note: Optional[str] = None


class DirectionsOutput(BaseModel):
    origin: str
    stops: List[str]
    total_km: Optional[float] = None
    total_duration_text: Optional[str] = None
    total_duration_minutes: Optional[int] = None
    maps_url: str
    maps_url_short: Optional[str] = None
    route_screenshot_path: Optional[str] = None
    transport_options: Optional[dict] = None


class FuelCostInput(BaseModel):
    distance_km: float = Field(gt=0, description="Total trip distance in kilometers.")
    fuel_type: str = Field(
        default="pertalite",
        description="One of: 'pertalite', 'pertamax', 'solar'. Defaults to pertalite.",
    )
    consumption_km_per_liter: Optional[float] = Field(
        default=None,
        gt=0,
        description="Optional override for car fuel economy (km per liter). Defaults to 12.",
    )


class FuelCostOutput(BaseModel):
    distance_km: float
    fuel_type: str
    consumption_km_per_liter: float
    liters_needed: float
    price_per_liter_idr: int
    total_cost_idr: int


class WebSearchInput(BaseModel):
    query: str = Field(
        description=(
            "Free-form search query. Use this for facts not on Google Maps, "
            "e.g. 'harga tiket Kebun Binatang Surabaya 2026'."
        )
    )
    max_results: int = Field(default=5, ge=1, le=10)


class WebSearchHit(BaseModel):
    title: str
    snippet: str
    url: str


class WebSearchOutput(BaseModel):
    query: str
    hits: List[WebSearchHit]


# ---------------------------------------------------------------------------
# Weather (BMKG)
# ---------------------------------------------------------------------------

class WeatherInput(BaseModel):
    city: str = Field(
        description=(
            "Nama kota atau kecamatan untuk cek cuaca, mis. 'Surabaya', 'Keputih Surabaya'. "
            "Gunakan kota destinasi utama perjalanan."
        )
    )
    travel_date: Optional[str] = Field(
        default=None,
        description=(
            "Tanggal rencana perjalanan format YYYY-MM-DD. "
            "Jika tidak disebutkan user, pakai tanggal hari ini."
        ),
    )


class WeatherForecast(BaseModel):
    datetime_local: str
    condition: str
    temp_celsius: Optional[int] = None
    humidity_percent: Optional[int] = None
    wind_speed_kmh: Optional[float] = None
    precipitation_mm: Optional[float] = None


class WeatherOutput(BaseModel):
    location_name: str
    province: str = ""
    forecasts: List[WeatherForecast] = Field(default_factory=list)
    source: str = "BMKG"
    error: Optional[str] = None
