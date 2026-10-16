"""Offline tests for otomoto_mcp. Run: python3 -m unittest test_otomoto_mcp.py"""

import json
import unittest
import urllib.parse

import otomoto_mcp as m


def next_data_html(page_props: dict) -> str:
    data = {"props": {"pageProps": page_props}}
    return (
        '<html><script id="__NEXT_DATA__" type="application/json" nonce="x">'
        f"{json.dumps(data)}</script></html>"
    )


def urql(*queries: dict) -> dict:
    return {"urqlState": {str(i): {"data": json.dumps(q)} for i, q in enumerate(queries)}}


def advert_node(price: str, year: str, mileage: str, currency: str = "PLN") -> dict:
    params = {"year": year, "mileage": mileage, "fuel_type": "diesel", "make": "skoda"}
    return {
        "id": "1",
        "title": "Skoda Octavia",
        "url": "https://www.otomoto.pl/osobowe/oferta/skoda-octavia-ID1.html",
        "price": {"amount": {"value": price, "currencyCode": currency}},
        "parameters": [{"key": k, "value": v} for k, v in params.items()],
        "location": {"city": {"name": "Tychy"}, "region": {"name": "Śląskie"}},
        "sellerLink": None,
        "priceDrop": {"percentage": 3.5},
    }


class BuildSearchUrlTest(unittest.TestCase):
    def params(self, **kwargs) -> dict:
        url = m.build_search_url(m.SearchFilters(**kwargs), "price_asc", page=2)
        return dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))

    def test_maps_filters_to_otomoto_query(self):
        q = self.params(
            make="skoda", model="octavia", price_max=90000, year_min=2020,
            fuel_type=["diesel", "hybrid"], seller_type="private",
        )
        self.assertEqual(q["search[filter_enum_make]"], "skoda")
        self.assertEqual(q["search[filter_enum_model]"], "octavia")
        self.assertEqual(q["search[filter_float_price:to]"], "90000")
        self.assertEqual(q["search[filter_float_year:from]"], "2020")
        self.assertEqual(q["search[filter_enum_fuel_type][0]"], "diesel")
        self.assertEqual(q["search[filter_enum_fuel_type][1]"], "hybrid")
        self.assertEqual(q["search[private_business]"], "private")
        self.assertEqual(q["search[filter_enum_damaged]"], "0")
        self.assertEqual(q["search[order]"], "filter_float_price:asc")
        self.assertEqual(q["page"], "2")
        self.assertNotIn("search[filter_float_price:from]", q)

    def test_damaged_offers_can_be_included(self):
        self.assertNotIn("search[filter_enum_damaged]", self.params(exclude_damaged=False))

    def test_model_requires_make(self):
        with self.assertRaises(m.OtomotoError):
            m.build_search_url(m.SearchFilters(model="golf"))


class ParseTest(unittest.TestCase):
    def test_parse_search(self):
        search = {
            "advertSearch": {
                "totalCount": 40,
                "pageInfo": {"currentOffset": 0},
                "edges": [{"node": advert_node("54900", "2020", "169000")}],
            }
        }
        data = m.extract_next_data(next_data_html(urql({"sortOptions": {}}, search)))
        result = m.parse_search(data)
        self.assertEqual(result["total_count"], 40)
        offer = result["offers"][0]
        self.assertEqual((offer["price"], offer["year"], offer["mileage_km"]), (54900, 2020, 169000))
        self.assertEqual(offer["seller"], "private seller")
        self.assertEqual(offer["price_drop_percent"], 3.5)
        self.assertEqual(offer["region"], "Śląskie")

    def test_missing_next_data_is_reported(self):
        with self.assertRaises(m.OtomotoError):
            m.extract_next_data("<html>captcha</html>")

    def test_filter_values_respects_conditions(self):
        def state(make, model_id):
            return {
                "filterId": "filter_enum_model",
                "conditions": [{"filterId": "filter_enum_make", "value": make}],
                "values": [{"values": [{"id": model_id, "name": model_id.title(), "counter": 5}]}],
            }

        data = {"props": {"pageProps": urql({"filters": {"states": [state("audi", "a4"), state("bmw", "x5")]}})}}
        values = m.filter_values(data, "filter_enum_model", {"filter_enum_make": "bmw"})
        self.assertEqual(values, [{"slug": "x5", "name": "X5", "offers": 5}])

    def test_parse_offer(self):
        advert = {
            "id": "9",
            "title": "Skoda",
            "price": {"value": "54900", "currency": "PLN", "labels": []},
            "details": [
                {"key": "mileage", "label": "Przebieg", "value": "169 000 km"},
                {"key": "vin", "label": "VIN", "value": "nI1Aaa7b.1.NfcnH=="},
            ],
            "parametersDict": {"has_vin": {"values": [{"value": "1"}]}},
            "equipment": [{"label": "Audio", "values": [{"label": "Radio"}]}],
            "seller": {"type": "PRIVATE", "name": "Jan"},
            "images": {"photos": [{}, {}]},
            "description": "<p>Stan&nbsp;bardzo dobry</p><p>Bezwypadkowy</p>",
        }
        offer = m.parse_offer({"props": {"pageProps": {"advert": advert}}})
        self.assertEqual(offer["price"], 54900)
        self.assertEqual(offer["details"], {"Przebieg": "169 000 km"})
        self.assertIs(offer["has_vin"], True)
        self.assertIsNone(offer["has_registration"])
        self.assertEqual(offer["equipment"], {"Audio": ["Radio"]})
        self.assertEqual(offer["photos"], 2)
        self.assertEqual(offer["description"], "Stan bardzo dobry\nBezwypadkowy")


class PriceReportTest(unittest.TestCase):
    def offer(self, price, year=2020, mileage=100_000, currency="PLN"):
        return m.summarize_offer(advert_node(str(price), str(year), str(mileage), currency))

    def test_stats_skip_outliers_and_foreign_currency(self):
        offers = [self.offer(p) for p in (50_000, 60_000, 70_000, 80_000)]
        offers.append(self.offer(1_784))  # monthly lease instalment
        offers.append(self.offer(20_000, currency="EUR"))
        report = m.compute_price_report(offers)
        overall = report["overall"]
        self.assertEqual((overall["count"], overall["min"], overall["max"]), (4, 50_000, 80_000))
        self.assertEqual(overall["median"], 65_000)
        self.assertEqual(report["skipped_price_outliers"], 1)
        self.assertEqual(report["skipped_non_pln_or_unpriced"], 1)
        self.assertEqual(report["cheapest"][0]["price"], 50_000)

    def test_breakdowns(self):
        offers = [self.offer(50_000, 2019, 160_000), self.offer(70_000, 2021, 40_000)]
        report = m.compute_price_report(offers)
        self.assertEqual(list(report["by_year"]), [2019, 2021])
        self.assertEqual(report["by_mileage"]["0-50k km"]["median"], 70_000)
        self.assertEqual(report["by_mileage"]["150-200k km"]["median"], 50_000)

    def test_single_value_and_empty(self):
        self.assertEqual(m.price_stats([42])["p25"], 42)
        self.assertEqual(m.price_stats([]), {"count": 0})


if __name__ == "__main__":
    unittest.main()
