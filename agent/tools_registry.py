"""Adapt our Playwright/fuel tools to LangChain StructuredTools.

LangChain's tool-calling agent expects coroutines tagged with their input
schemas. Each adapter:
- Logs the call (huge help when debugging the ReAct loop).
- Catches exceptions so a single Playwright timeout doesn't abort the agent;
  it returns an error string the LLM can react to ('I need to try again').
- Serializes the Pydantic output to a plain dict — easier for the LLM to read.
"""

from __future__ import annotations

import logging
from typing import List, Optional

from langchain_core.tools import StructuredTool

from tools.maps_details import get_place_details as _get_place_details
from tools.maps_directions import get_directions as _get_directions
from tools.maps_search import search_places_on_maps as _search_places
from tools.schemas import (
    DirectionsInput,
    PlaceDetailsInput,
    SearchPlacesInput,
    WeatherInput,
    WebSearchInput,
)
from tools.weather import get_weather as _get_weather
from tools.web_search import web_search as _web_search

logger = logging.getLogger(__name__)


async def _wrap_search_places(query: str, city: str, limit: int = 6):
    logger.info("[tool] search_places_on_maps(%r, %r, limit=%d)", query, city, limit)
    try:
        result = await _search_places(query=query, city=city, limit=limit)
        return result.model_dump()
    except Exception as e:
        logger.exception("search_places_on_maps failed")
        return {"error": f"search_places_on_maps gagal: {e}"}


async def _wrap_place_details(name: str, city: str):
    logger.info("[tool] get_place_details(%r, %r)", name, city)
    try:
        result = await _get_place_details(name=name, city=city)
        return result.model_dump()
    except Exception as e:
        logger.exception("get_place_details failed")
        return {"error": f"get_place_details gagal: {e}"}


async def _wrap_directions(origin: str, waypoints: List[str]):
    logger.info("[tool] get_directions(%r, %r)", origin, waypoints)
    try:
        result = await _get_directions(origin=origin, waypoints=waypoints)
        return result.model_dump()
    except Exception as e:
        logger.exception("get_directions failed")
        return {"error": f"get_directions gagal: {e}"}


async def _wrap_weather(city: str, travel_date: Optional[str] = None):
    logger.info("[tool] get_weather(city=%r, travel_date=%r)", city, travel_date)
    try:
        result = await _get_weather(city=city, travel_date=travel_date)
        return result.model_dump()
    except Exception as e:
        logger.exception("get_weather failed")
        return {"error": f"get_weather gagal: {e}"}


async def _wrap_web_search(query: str, max_results: int = 5):
    logger.info("[tool] web_search(%r, max_results=%d)", query, max_results)
    try:
        result = await _web_search(query=query, max_results=max_results)
        return result.model_dump()
    except Exception as e:
        logger.exception("web_search failed")
        return {"error": f"web_search gagal: {e}"}


def build_tools() -> List[StructuredTool]:
    return [
        StructuredTool.from_function(
            name="search_places_on_maps",
            description=(
                "Cari daftar tempat di Google Maps berdasarkan kategori dan kota. "
                "Gunakan ini sebagai langkah pertama untuk mengumpulkan kandidat "
                "tempat wisata, kuliner, atau penginapan."
            ),
            coroutine=_wrap_search_places,
            args_schema=SearchPlacesInput,
        ),
        StructuredTool.from_function(
            name="get_place_details",
            description=(
                "Ambil detail lengkap satu tempat: jam buka per hari (penting "
                "untuk cek apakah tempat buka di hari yang diminta user), rating, "
                "kategori, telepon, website. Pakai untuk memvalidasi setiap "
                "kandidat dari search_places_on_maps."
            ),
            coroutine=_wrap_place_details,
            args_schema=PlaceDetailsInput,
        ),
        StructuredTool.from_function(
            name="get_directions",
            description=(
                "Buka Google Maps Directions untuk origin + daftar waypoint, lalu "
                "ambil total jarak (km), total durasi berkendara, dan URL deep-link "
                "ke Google Maps. Urutan waypoint = urutan kunjungan yang akan "
                "dipakai. Susun urutan secara cerdas sebelum memanggil tool ini."
            ),
            coroutine=_wrap_directions,
            args_schema=DirectionsInput,
        ),
        StructuredTool.from_function(
            name="get_weather",
            description=(
                "Ambil prediksi cuaca dari BMKG untuk kota tujuan perjalanan. "
                "Panggil setelah mengetahui kota destinasi dan tanggal perjalanan. "
                "Gunakan nama kota dalam Bahasa Indonesia, mis. 'Surabaya', 'Bandung'. "
                "travel_date format YYYY-MM-DD; jika tidak disebutkan user, pakai hari ini."
            ),
            coroutine=_wrap_weather,
            args_schema=WeatherInput,
        ),
        StructuredTool.from_function(
            name="web_search",
            description=(
                "Cari informasi via Google Search untuk hal yang tidak ada di "
                "Google Maps, misalnya harga tiket masuk terkini, harga BBM, "
                "rekomendasi blog, atau info acara/event."
            ),
            coroutine=_wrap_web_search,
            args_schema=WebSearchInput,
        ),
    ]
