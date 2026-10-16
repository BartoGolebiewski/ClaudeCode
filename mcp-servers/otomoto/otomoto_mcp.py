# /// script
# requires-python = ">=3.11"
# dependencies = ["mcp>=2,<3"]
# ///
"""OtoMoto MCP server: search passenger-car offers on otomoto.pl and analyse prices.

OtoMoto has no public API. This server reads the JSON that otomoto.pl embeds in
its public pages (the Next.js `__NEXT_DATA__` script), the same data the website
renders. No login is needed and nothing is written. Requests are made one at a
time with a short pause between pages, so keep page counts small.
"""

from __future__ import annotations

import asyncio
import json
import re
import statistics
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field

BASE_URL = "https://www.otomoto.pl"
CATEGORY = "osobowe"
PAGE_SIZE = 32
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)
REQUEST_TIMEOUT_S = 30
PAGE_DELAY_S = 1.0
OUTLIER_FRACTION = 0.25  # prices below this share of the median are left out of stats
NEXT_DATA_RE = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)

SortOrder = Literal[
    "relevance", "newest", "price_asc", "price_desc", "mileage_asc", "mileage_desc"
]
SORT_IDS: dict[str, str] = {
    "relevance": "relevance_web",
    "newest": "created_at_first:desc",
    "price_asc": "filter_float_price:asc",
    "price_desc": "filter_float_price:desc",
    "mileage_asc": "filter_float_mileage:asc",
    "mileage_desc": "filter_float_mileage:desc",
}
FuelType = Literal[
    "petrol", "petrol-lpg", "petrol-cng", "diesel", "hybrid", "plugin-hybrid",
    "electric", "etanol", "hidrogen",
]
BodyType = Literal[
    "mini", "city-car", "coupe", "cabrio", "combi", "compact", "minivan", "sedan", "suv"
]
OFFER_PARAM_KEYS = (
    "make", "model", "version", "year", "mileage", "fuel_type", "gearbox",
    "engine_capacity", "engine_power",
)
NO_RESULTS_HINT = (
    "No offers match. An unknown make, model or generation slug also gives zero results, "
    "so check slugs with the otomoto_list_* tools, or relax the filters."
)
READ_ONLY = ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True
)

mcp = MCPServer(
    "otomoto_mcp",
    instructions=(
        "Search passenger-car offers on otomoto.pl (Poland) and analyse their prices. "
        "Make, model and generation filters take OtoMoto slugs (e.g. 'alfa-romeo', "
        "'corolla', 'gen-xii-2018'); look them up with otomoto_list_makes, "
        "otomoto_list_models and otomoto_list_generations when unsure. Prices are in PLN."
    ),
)


class OtomotoError(ToolError):
    """Reported to the client as a tool error; the message tells the agent what to do."""


# --------------------------------------------------------------------------- fetching


def _fetch_html(url: str) -> str:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept-Language": "pl-PL,pl;q=0.9"},
    )
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_S) as response:
            return response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise OtomotoError(
                f"Page not found (404): {url}. The offer may have ended, or a make/model "
                "slug is wrong; check slugs with otomoto_list_makes / otomoto_list_models."
            ) from e
        if e.code in (403, 429):
            raise OtomotoError(
                f"OtoMoto refused the request (HTTP {e.code}); it is rate limiting. "
                "Wait a minute and request fewer pages."
            ) from e
        raise OtomotoError(f"OtoMoto returned HTTP {e.code} for {url}.") from e
    except (urllib.error.URLError, TimeoutError) as e:
        raise OtomotoError(f"Could not reach otomoto.pl: {e}") from e


def extract_next_data(html: str) -> dict[str, Any]:
    match = NEXT_DATA_RE.search(html)
    if not match:
        raise OtomotoError(
            "The page has no embedded data (__NEXT_DATA__). OtoMoto may have changed "
            "its page layout or shown a captcha."
        )
    return json.loads(match.group(1))


def urql_queries(next_data: dict[str, Any]) -> list[dict[str, Any]]:
    """Decode the GraphQL query results cached in the page (urqlState)."""
    state = next_data.get("props", {}).get("pageProps", {}).get("urqlState") or {}
    results = []
    for entry in state.values():
        raw = entry.get("data")
        if raw:
            results.append(json.loads(raw))
    return results


async def fetch_next_data(url: str) -> dict[str, Any]:
    html = await asyncio.to_thread(_fetch_html, url)
    return extract_next_data(html)


# --------------------------------------------------------------------------- search


class SearchFilters(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    make: str | None = Field(
        default=None, description="Make slug, e.g. 'toyota', 'bmw', 'alfa-romeo'."
    )
    model: str | None = Field(
        default=None, description="Model slug, e.g. 'corolla', 'seria-3'. Needs make."
    )
    generation: str | None = Field(
        default=None,
        description="Generation slug from otomoto_list_generations, e.g. 'gen-xii-2018'.",
    )
    price_min: int | None = Field(default=None, ge=0, description="Minimum price, PLN.")
    price_max: int | None = Field(default=None, ge=0, description="Maximum price, PLN.")
    year_min: int | None = Field(default=None, ge=1900, le=2100, description="Oldest production year.")
    year_max: int | None = Field(default=None, ge=1900, le=2100, description="Newest production year.")
    mileage_min: int | None = Field(default=None, ge=0, description="Minimum mileage, km.")
    mileage_max: int | None = Field(default=None, ge=0, description="Maximum mileage, km.")
    power_min: int | None = Field(default=None, ge=0, description="Minimum engine power, KM (hp).")
    power_max: int | None = Field(default=None, ge=0, description="Maximum engine power, KM (hp).")
    fuel_type: list[FuelType] | None = Field(default=None, description="One or more fuel types.")
    body_type: list[BodyType] | None = Field(default=None, description="One or more body types.")
    gearbox: Literal["manual", "automatic"] | None = Field(default=None)
    condition: Literal["used", "new"] | None = Field(default=None)
    seller_type: Literal["private", "business"] | None = Field(default=None)
    exclude_damaged: bool = Field(default=True, description="Hide offers marked as damaged.")


def build_search_url(filters: SearchFilters, sort: str = "relevance", page: int = 1) -> str:
    if filters.model and not filters.make:
        raise OtomotoError("'model' needs 'make' as well.")
    if filters.generation and not filters.model:
        raise OtomotoError("'generation' needs 'make' and 'model' as well.")

    params: list[tuple[str, str]] = []

    def add(key: str, value: object) -> None:
        if value is not None:
            params.append((f"search[{key}]", str(value)))

    add("filter_enum_make", filters.make)
    add("filter_enum_model", filters.model)
    add("filter_enum_generation", filters.generation)
    for name in ("price", "year", "mileage"):
        add(f"filter_float_{name}:from", getattr(filters, f"{name}_min"))
        add(f"filter_float_{name}:to", getattr(filters, f"{name}_max"))
    add("filter_float_engine_power:from", filters.power_min)
    add("filter_float_engine_power:to", filters.power_max)
    for i, fuel in enumerate(filters.fuel_type or []):
        add(f"filter_enum_fuel_type][{i}", fuel)
    for i, body in enumerate(filters.body_type or []):
        add(f"filter_enum_body_type][{i}", body)
    add("filter_enum_gearbox", filters.gearbox)
    add("new_used", filters.condition)
    add("private_business", filters.seller_type)
    if filters.exclude_damaged:
        add("filter_enum_damaged", 0)
    add("order", SORT_IDS[sort])
    if page > 1:
        params.append(("page", str(page)))
    return f"{BASE_URL}/{CATEGORY}?{urllib.parse.urlencode(params)}"


def _param_values(node: dict[str, Any]) -> dict[str, str]:
    return {p["key"]: p["value"] for p in node.get("parameters") or [] if p.get("key")}


def _int_or_none(value: str | None) -> int | None:
    try:
        return int(float(value)) if value not in (None, "") else None
    except ValueError:
        return None


def summarize_offer(node: dict[str, Any]) -> dict[str, Any]:
    params = _param_values(node)
    amount = (node.get("price") or {}).get("amount") or {}
    location = node.get("location") or {}
    seller = node.get("sellerLink") or {}
    price_drop = node.get("priceDrop") or {}
    return {
        "id": node.get("id"),
        "title": node.get("title"),
        "url": node.get("url"),
        "price": _int_or_none(amount.get("value")),
        "currency": amount.get("currencyCode"),
        "price_drop_percent": price_drop.get("percentage"),
        "year": _int_or_none(params.get("year")),
        "mileage_km": _int_or_none(params.get("mileage")),
        "fuel_type": params.get("fuel_type"),
        "gearbox": params.get("gearbox"),
        "engine_capacity_cm3": _int_or_none(params.get("engine_capacity")),
        "engine_power_hp": _int_or_none(params.get("engine_power")),
        "version": params.get("version"),
        "city": (location.get("city") or {}).get("name"),
        "region": (location.get("region") or {}).get("name"),
        "seller": seller.get("name") or "private seller",
        "created_at": node.get("createdAt"),
        "short_description": node.get("shortDescription"),
    }


def parse_search(next_data: dict[str, Any]) -> dict[str, Any]:
    for query in urql_queries(next_data):
        search = query.get("advertSearch")
        if search is not None:
            return {
                "total_count": search.get("totalCount") or 0,
                "offset": (search.get("pageInfo") or {}).get("currentOffset") or 0,
                "offers": [
                    summarize_offer(edge["node"])
                    for edge in search.get("edges") or []
                    if edge.get("node")
                ],
            }
    raise OtomotoError("No search results found in the page data; the page layout may have changed.")


async def run_search(filters: SearchFilters, sort: str, page: int) -> dict[str, Any]:
    url = build_search_url(filters, sort, page)
    result = parse_search(await fetch_next_data(url))
    result["search_url"] = url
    return result


def _dump(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=1)


@mcp.tool(name="otomoto_search_offers", annotations=READ_ONLY)
async def otomoto_search_offers(
    filters: SearchFilters,
    sort: SortOrder = "relevance",
    page: int = Field(default=1, ge=1, le=500, description="Results page, 32 offers per page."),
) -> str:
    """Search passenger-car offers on otomoto.pl.

    Returns total_count (all matching offers), has_more, search_url (the same search
    on the website) and up to 32 offers per page, each with id, title, url, price (PLN),
    year, mileage_km, fuel_type, gearbox, engine data, city, region and seller.
    Use otomoto_get_offer with an offer url for the full description and equipment.
    """
    result = await run_search(filters, sort, page)
    result["page"] = page
    result["has_more"] = result["offset"] + len(result["offers"]) < result["total_count"]
    if result["total_count"] == 0:
        result["hint"] = NO_RESULTS_HINT
    return _dump(result)


# --------------------------------------------------------------------------- price stats


def _percentile(sorted_values: list[int], q: float) -> float:
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    return statistics.quantiles(sorted_values, n=100, method="inclusive")[int(q * 100) - 1]


def price_stats(prices: list[int]) -> dict[str, Any]:
    if not prices:
        return {"count": 0}
    values = sorted(prices)
    return {
        "count": len(values),
        "min": values[0],
        "p25": round(_percentile(values, 0.25)),
        "median": round(statistics.median(values)),
        "mean": round(statistics.fmean(values)),
        "p75": round(_percentile(values, 0.75)),
        "max": values[-1],
    }


def compute_price_report(offers: list[dict[str, Any]]) -> dict[str, Any]:
    pln = [o for o in offers if o["price"] is not None and o["currency"] == "PLN"]
    # Lease takeovers and similar offers list a monthly instalment as the price.
    floor = statistics.median(o["price"] for o in pln) * OUTLIER_FRACTION if pln else 0
    priced = [o for o in pln if o["price"] >= floor]
    by_year: dict[int, list[int]] = {}
    for offer in priced:
        if offer["year"] is not None:
            by_year.setdefault(offer["year"], []).append(offer["price"])
    with_mileage = [o for o in priced if o["mileage_km"] is not None]
    mileage_bands = [(0, 50_000), (50_000, 100_000), (100_000, 150_000), (150_000, 200_000), (200_000, None)]
    by_mileage = {}
    for low, high in mileage_bands:
        band = [o["price"] for o in with_mileage if o["mileage_km"] >= low and (high is None or o["mileage_km"] < high)]
        if band:
            label = f"{low // 1000}k+ km" if high is None else f"{low // 1000}-{high // 1000}k km"
            by_mileage[label] = price_stats(band)
    cheapest = sorted(priced, key=lambda o: o["price"])[:3]
    return {
        "overall": price_stats([o["price"] for o in priced]),
        "by_year": {year: price_stats(p) for year, p in sorted(by_year.items())},
        "by_mileage": by_mileage,
        "cheapest": [
            {k: o[k] for k in ("title", "price", "year", "mileage_km", "url")} for o in cheapest
        ],
        "skipped_non_pln_or_unpriced": len(offers) - len(pln),
        "skipped_price_outliers": len(pln) - len(priced),
    }


@mcp.tool(name="otomoto_price_stats", annotations=READ_ONLY)
async def otomoto_price_stats(
    filters: SearchFilters,
    max_offers: int = Field(
        default=160, ge=32, le=640,
        description="How many matching offers to sample (fetched 32 per page, about 1 s per page).",
    ),
) -> str:
    """Price statistics (PLN) for offers matching the filters.

    Samples the newest matching offers and returns min, p25, median, mean, p75 and max
    overall, broken down by production year and by mileage band, plus the 3 cheapest
    offers. total_count tells how many offers match in all; when it exceeds the sample,
    narrow the filters (e.g. a year range) for a more precise picture. Prices below a
    quarter of the median (usually lease takeovers quoting a monthly instalment) are
    left out and counted in skipped_price_outliers.
    """
    offers: list[dict[str, Any]] = []
    total = 0
    url = ""
    try:
        page = 1
        while len(offers) < max_offers:
            if page > 1:
                await asyncio.sleep(PAGE_DELAY_S)
            result = await run_search(filters, "newest", page)
            if page == 1:
                total, url = result["total_count"], result["search_url"]
            offers.extend(result["offers"])
            if not result["offers"] or len(offers) >= total:
                break
            page += 1
    except OtomotoError:
        if not offers:
            raise
    offers = offers[:max_offers]
    report: dict[str, Any] = {"total_count": total, "sampled": len(offers), "search_url": url}
    if not offers:
        report["hint"] = NO_RESULTS_HINT
    report.update(compute_price_report(offers))
    return _dump(report)


# --------------------------------------------------------------------------- catalogue


def filter_values(
    next_data: dict[str, Any], filter_id: str, conditions: dict[str, str] | None = None
) -> list[dict[str, Any]]:
    """Values of one search filter, e.g. all makes or the models of one make."""
    conditions = conditions or {}
    for query in urql_queries(next_data):
        for state in (query.get("filters") or {}).get("states") or []:
            if state.get("filterId") != filter_id:
                continue
            state_conditions = {c["filterId"]: c["value"] for c in state.get("conditions") or []}
            if state_conditions != conditions:
                continue
            return [
                {"slug": v["id"], "name": v["name"], "offers": v.get("counter")}
                for group in state.get("values") or []
                for v in group.get("values") or []
            ]
    return []


@mcp.tool(name="otomoto_list_makes", annotations=READ_ONLY)
async def otomoto_list_makes() -> str:
    """All car makes on OtoMoto with their slugs (for the 'make' filter)."""
    values = filter_values(await fetch_next_data(f"{BASE_URL}/{CATEGORY}"), "filter_enum_make")
    return _dump([{"slug": v["slug"], "name": v["name"]} for v in values])


@mcp.tool(name="otomoto_list_models", annotations=READ_ONLY)
async def otomoto_list_models(
    make: str = Field(description="Make slug, e.g. 'volkswagen'."),
) -> str:
    """Models of one make with their slugs and current offer counts."""
    make = make.strip().lower()
    data = await fetch_next_data(f"{BASE_URL}/{CATEGORY}/{urllib.parse.quote(make)}")
    values = filter_values(data, "filter_enum_model", {"filter_enum_make": make})
    if not values:
        raise OtomotoError(f"No models found for make '{make}'. Check the slug with otomoto_list_makes.")
    return _dump(values)


@mcp.tool(name="otomoto_list_generations", annotations=READ_ONLY)
async def otomoto_list_generations(
    make: str = Field(description="Make slug, e.g. 'volkswagen'."),
    model: str = Field(description="Model slug, e.g. 'golf'."),
) -> str:
    """Generations of one model with their slugs and offer counts. Not every model has them."""
    make, model = make.strip().lower(), model.strip().lower()
    url = f"{BASE_URL}/{CATEGORY}/{urllib.parse.quote(make)}/{urllib.parse.quote(model)}"
    data = await fetch_next_data(url)
    values = filter_values(
        data, "filter_enum_generation", {"filter_enum_make": make, "filter_enum_model": model}
    )
    if not values:
        return f"No generations listed for {make} {model}; filter by year_min/year_max instead."
    return _dump(values)


# --------------------------------------------------------------------------- offer details


def _strip_html(text: str) -> str:
    text = re.sub(r"<br\s*/?>|</p>|</li>", "\n", text or "")
    text = re.sub(r"<[^>]+>", "", text)
    for entity, char in (("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"')):
        text = text.replace(entity, char)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


HIDDEN_DETAIL_KEYS = {"vin"}


def _flag(advert: dict[str, Any], key: str) -> bool | None:
    values = ((advert.get("parametersDict") or {}).get(key) or {}).get("values") or []
    return values[0].get("value") == "1" if values else None


def parse_offer(next_data: dict[str, Any]) -> dict[str, Any]:
    advert = next_data.get("props", {}).get("pageProps", {}).get("advert")
    if not advert:
        raise OtomotoError("No offer data on this page. Is it an OtoMoto offer URL (…/oferta/…)?")
    price = advert.get("price") or {}
    seller = advert.get("seller") or {}
    return {
        "id": advert.get("id"),
        "status": advert.get("status"),
        "title": advert.get("title"),
        "url": advert.get("url"),
        "price": _int_or_none(price.get("value")),
        "currency": price.get("currency"),
        "price_labels": price.get("labels"),
        "created_at": advert.get("createdAt"),
        "details": {
            d["label"]: d["value"]
            for d in advert.get("details") or []
            if d.get("label") and d.get("key") not in HIDDEN_DETAIL_KEYS
        },
        # The VIN itself is only shown to logged-in users; the page carries just a ciphertext.
        "has_vin": _flag(advert, "has_vin"),
        "has_registration": _flag(advert, "has_registration"),
        "equipment": {
            group["label"]: [v["label"] for v in group.get("values") or []]
            for group in advert.get("equipment") or []
        },
        "seller": {
            "type": seller.get("type"),
            "name": seller.get("name"),
            "badges": [b.get("label") for b in seller.get("featuresBadges") or []],
        },
        "photos": len((advert.get("images") or {}).get("photos") or []),
        "description": _strip_html(advert.get("description") or ""),
    }


@mcp.tool(name="otomoto_get_offer", annotations=READ_ONLY)
async def otomoto_get_offer(
    url: str = Field(
        description="Offer URL from otomoto_search_offers, e.g. "
        "'https://www.otomoto.pl/osobowe/oferta/toyota-corolla-ID6Ihe0F.html'.",
    ),
) -> str:
    """Full details of one offer: technical data, equipment, seller and description."""
    url = url.strip()
    if not url.startswith(f"{BASE_URL}/"):
        raise OtomotoError(f"Expected a URL starting with {BASE_URL}/, got '{url}'.")
    return _dump(parse_offer(await fetch_next_data(url)))


if __name__ == "__main__":
    mcp.run()
