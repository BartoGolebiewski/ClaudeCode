# OtoMoto MCP

MCP server do wyszukiwania ofert samochodów osobowych na [otomoto.pl](https://www.otomoto.pl) i analizy cen.

OtoMoto nie ma publicznego API. Serwer czyta dane JSON, które strona osadza w swoich publicznych podstronach (`__NEXT_DATA__`), czyli te same dane, które widać w przeglądarce. Nie wymaga logowania i niczego nie zapisuje. Jeśli OtoMoto zmieni układ stron, parser może wymagać poprawek.

## Narzędzia

| Narzędzie | Co robi |
|---|---|
| `otomoto_search_offers` | Wyszukiwanie z filtrami: marka, model, generacja, cena, rocznik, przebieg, moc, paliwo, nadwozie, skrzynia, stan, typ sprzedawcy. Sortowanie i stronicowanie (32 oferty na stronę). |
| `otomoto_price_stats` | Statystyki cen (min, p25, mediana, średnia, p75, max) dla próbki najnowszych ofert, w podziale na roczniki i przedziały przebiegu, plus 3 najtańsze oferty. |
| `otomoto_get_offer` | Szczegóły oferty: dane techniczne, wyposażenie, sprzedawca, opis. |
| `otomoto_list_makes` | Lista marek i ich slugów. |
| `otomoto_list_models` | Modele danej marki z liczbą ofert. |
| `otomoto_list_generations` | Generacje danego modelu (np. `gen-iv-2019-octavia`). |

Filtry marki, modelu i generacji przyjmują slugi OtoMoto (np. `alfa-romeo`, `seria-3`). Nieznany slug daje po prostu 0 wyników, więc w razie wątpliwości użyj narzędzi `otomoto_list_*`.

`otomoto_price_stats` pomija ceny poniżej 25% mediany. Zwykle są to cesje leasingu, gdzie jako cenę podano ratę miesięczną.

## Użycie

Serwer jest zarejestrowany w `.mcp.json` w katalogu głównym repozytorium, więc Claude Code w tym repo zaproponuje jego włączenie. Potrzebny jest tylko [uv](https://docs.astral.sh/uv/), który sam zainstaluje zależności (`mcp>=2`):

```bash
uv run --script mcp-servers/otomoto/otomoto_mcp.py
```

Aby dodać go globalnie do Claude Code:

```bash
claude mcp add otomoto -- uv run --script /pełna/ścieżka/mcp-servers/otomoto/otomoto_mcp.py
```

W Claude Desktop (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "otomoto": {
      "command": "uv",
      "args": ["run", "--script", "/pełna/ścieżka/mcp-servers/otomoto/otomoto_mcp.py"]
    }
  }
}
```

## Testy

```bash
cd mcp-servers/otomoto
uv run --with "mcp>=2,<3" python -m unittest test_otomoto_mcp.py
```

Testy działają offline, na spreparowanych danych.

## Uwagi

- Zapytania idą po kolei z 1 s przerwy między stronami. `otomoto_price_stats` pobiera domyślnie 5 stron (160 ofert). Przy masowym pobieraniu OtoMoto może zacząć blokować (HTTP 403/429).
- Korzystaj zgodnie z regulaminem OtoMoto, do własnego użytku.
