"""FX rate retrieval for Lavender Report.

Rates come from the French customs (douane.gouv.fr): the official
monthly conversion rates usable for VAT declarations. The consultation
service is queried per country of origin and date, and answers in the
"1 Euro = X <DEVISE>" direction.

Rates are immutable once published, so fetched rates are cached in
.cache/fx_rates.json and never re-fetched. Manual --fx-rate overrides
take precedence over the network and are not cached.
"""

import re
import urllib.parse
import urllib.request
from decimal import Decimal
from http.cookiejar import CookieJar

from cache import load as cache_load
from cache import save as cache_save

BASE_URL = "https://www.douane.gouv.fr/debweb/cf.srv"

# The customs service is queried by country of origin, not by currency;
# map the common currencies to the country code the service expects.
COUNTRY_BY_CURRENCY = {
    "AUD": "AU",
    "BGN": "BG",
    "BRL": "BR",
    "CAD": "CA",
    "CHF": "CH",
    "CNY": "CN",
    "CZK": "CZ",
    "DKK": "DK",
    "GBP": "GB",
    "HKD": "HK",
    "HUF": "HU",
    "ILS": "IL",
    "INR": "IN",
    "JPY": "JP",
    "KRW": "KR",
    "MXN": "MX",
    "NOK": "NO",
    "NZD": "NZ",
    "PLN": "PL",
    "RON": "RO",
    "SEK": "SE",
    "SGD": "SG",
    "THB": "TH",
    "TRY": "TR",
    "USD": "US",
    "ZAR": "ZA",
}

FX_CACHE_VERSION = 2

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) Gecko/20100101 Firefox/130.0"

# "1 Euro= 1,1605 USD" radio entries in the result page
RATE_RE = re.compile(r"1\s+Euro\s*=\s*([0-9]+(?:[.,][0-9]+)?)\s+([A-Z]{3})")


class DouaneRateError(Exception):
    """Failure to obtain a rate from douane.gouv.fr."""


def _load_cache():
    return cache_load("fx_rates", FX_CACHE_VERSION)


def _save_cache(rates):
    cache_save("fx_rates", FX_CACHE_VERSION, rates)


def _open(opener, url, referer=None):
    headers = {"User-Agent": USER_AGENT}
    if referer:
        headers["Referer"] = referer
    request = urllib.request.Request(url, headers=headers)
    with opener.open(request, timeout=30) as response:
        return response.read().decode("iso-8859-1")


def fetch_douane_rate(currency, date_str):
    """Fetch the official rate for a currency at a date (YYYY-MM-DD).

    Returns the rate as a Decimal in the douane direction
    (1 EUR = <rate> <currency>), or raises DouaneRateError.
    """
    country = COUNTRY_BY_CURRENCY.get(currency)
    if not country:
        raise DouaneRateError(
            f"no douane.gouv.fr country code known for {currency}; "
            f"provide it with --fx-rate {currency}=<rate>"
        )

    # The consultation flow requires an established session: menu,
    # form init, then the query itself (with the form page as referer)
    jar = CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    date_saisie = date_str[8:10] + date_str[5:7] + date_str[0:4]  # JJMMAAAA
    try:
        _open(opener, BASE_URL + "?etape=menuTaux&")
        init_url = BASE_URL + "?etape=initRechTauxTaux"
        _open(opener, init_url)
        query = urllib.parse.urlencode({
            "etape": "rechercheTauxTaux",
            "paysOrigine": country,
            "dateSaisie": date_saisie,
            "suivant": "suivant",
        })
        page = _open(opener, BASE_URL + "?" + query, referer=init_url)
    except OSError as e:
        raise DouaneRateError(f"douane.gouv.fr request failed: {e}") from e

    if "Erreur session" in page:
        raise DouaneRateError("douane.gouv.fr returned a session error")

    rates = {}
    for value, code in RATE_RE.findall(page):
        rates[code] = Decimal(value.replace(",", "."))
    if currency not in rates:
        raise DouaneRateError(
            f"douane.gouv.fr returned no rate for {currency} on {date_str}"
        )
    return rates[currency]


def get_rates(currencies, date_str, overrides=None):
    """Resolve a conversion rate for every requested currency.

    Returns (rates, errors): rates maps currency -> Decimal
    (1 EUR = <rate> <currency>; EUR itself is 1), errors maps
    currency -> message for currencies that could not be resolved.
    Manual overrides (same douane direction) win over everything else.
    """
    overrides = overrides or {}
    cache = _load_cache()
    rates = {"EUR": Decimal(1)}
    errors = {}
    cache_dirty = False

    for currency in sorted(set(currencies)):
        if currency == "EUR":
            continue
        if currency in overrides:
            rates[currency] = overrides[currency]
            continue
        key = f"{currency}:{date_str}"
        if key in cache:
            rates[currency] = Decimal(cache[key]["rate"])
            continue
        try:
            rate = fetch_douane_rate(currency, date_str)
        except DouaneRateError as e:
            errors[currency] = str(e)
            continue
        cache[key] = {"rate": str(rate), "date": date_str, "source": "douane"}
        cache_dirty = True
        rates[currency] = rate

    if cache_dirty:
        _save_cache(cache)
    return rates, errors


def parse_override(spec):
    """Parse a --fx-rate CUR=RATE argument (douane direction:
    1 EUR = RATE CUR). Returns (currency, Decimal rate)."""
    if "=" not in spec:
        raise ValueError(f"invalid --fx-rate '{spec}', expected CUR=RATE (e.g. USD=1.16)")
    currency, _, value = spec.partition("=")
    currency = currency.strip().upper()
    if not re.fullmatch(r"[A-Z]{3}", currency):
        raise ValueError(f"invalid currency in --fx-rate '{spec}'")
    try:
        rate = Decimal(value.strip())
    except Exception as e:
        raise ValueError(f"invalid rate in --fx-rate '{spec}'") from e
    if rate <= 0:
        raise ValueError(f"rate must be positive in --fx-rate '{spec}'")
    return currency, rate
