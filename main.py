import argparse
import calendar
import csv
import os
import re
import sys
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

import pytz
import stripe
from dotenv import load_dotenv

from cache import load as cache_load
from cache import save as cache_save
from fx_rates import get_rates, parse_override
from report_csv import generate_csv_report
from report_html import generate_html_report

load_dotenv()

# Stripe secret key
stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
if stripe.api_key is None:
    print("Error: STRIPE_SECRET_KEY environment variable not set.", file=sys.stderr)
    sys.exit(1)

# Parse arguments; the default period is the previous month,
# rolling the year back when the current month is January
now = datetime.now()
if now.month == 1:
    default_year = now.year - 1
    default_month = 12
else:
    default_year = now.year
    default_month = now.month - 1

parser = argparse.ArgumentParser(description="Lavender Report")
parser.add_argument('--country', type=str, help="Country", default="FR")
parser.add_argument('--year', type=int, help="Year", default=default_year)
parser.add_argument('--month', type=int, help="Month", default=default_month)
parser.add_argument('--export', type=str, choices=['csv', 'html'], help="Export format (csv or html)")
parser.add_argument('--output', type=str, help="Output filename for export")
parser.add_argument('--debug', action='store_true', help="Log balance transactions skipped by the type filter")
parser.add_argument('--currency', type=str, help="Default report currency (default: EUR). All currencies are reported; non-default ones are converted at the official douane.gouv.fr monthly rate", default="EUR")
parser.add_argument('--fx-rate', type=str, action='append', metavar='CUR=RATE', help="Manual exchange rate for a currency, in the douane direction (1 EUR = RATE CUR, e.g. USD=1.16). Repeatable; takes precedence over douane.gouv.fr")
parser.add_argument('--locale', type=str, choices=['en', 'fr'], help="Number formatting for display (en: 3571.65 EUR, fr: 3 571,65 EUR with euro sign)", default="en")
args = parser.parse_args()

# Validate arguments before touching the Stripe API
if not 1 <= args.month <= 12:
    parser.error("--month must be between 1 and 12")
if not re.fullmatch(r"[A-Za-z]{2}", args.country):
    parser.error("--country must be a two-letter ISO 3166-1 code (e.g. FR)")

# Manual exchange-rate overrides (douane direction: 1 EUR = RATE CUR)
fx_overrides = {}
for spec in (args.fx_rate or []):
    try:
        currency, rate = parse_override(spec)
    except ValueError as e:
        parser.error(str(e))
    fx_overrides[currency] = rate

arg_country = args.country.upper()
arg_year = args.year
arg_month = args.month
arg_currency = args.currency.upper()
arg_locale = args.locale
export_format = args.export
output_filename = args.output

# EU member states (the company's own country is handled separately)
EU_COUNTRIES = [
    "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "DE", "GR", "HU",
    "IE", "IT", "LV", "LT", "LU", "MT", "NL", "PL", "PT", "RO", "SK", "SI",
    "ES", "SE"
]

# Charge-details cache: the per-charge API lookups (customer, invoice,
# tax rates) return immutable data, so persist them across runs.
# Bump CACHE_VERSION whenever the extraction logic below changes, so
# stale entries are discarded.
CACHE_VERSION = 4


CURRENCY_SYMBOLS = {"EUR": "€", "USD": "$", "GBP": "£"}


def format_amount(value, currency):
    """Format a money amount for display according to --locale."""
    if arg_locale == 'fr':
        symbol = CURRENCY_SYMBOLS.get(currency, currency)
        text = f"{value:,.2f}".replace(",", "\u00a0").replace(".", ",")
        return f"{text} {symbol}"
    return f"{value:.2f} {currency}"


def ht_amount(t):
    """HT (ex-VAT) amount of a transaction detail: TTC minus charged tax."""
    return t["amount"] - t["tax_amount"]

# Convert dates in timestamps (UTC+1)
def to_timestamp(date_str):
    tz = pytz.timezone("Europe/Paris")  # UTC+1
    dt = datetime.strptime(date_str, "%Y-%m-%d")  # Format to YYYY-MM-DD
    dt_utc = tz.localize(dt).astimezone(pytz.utc)  # Convert to UTC
    return int(dt_utc.timestamp())

# Find first and last day of month
start_date = f"{arg_year}-{arg_month:02d}-01"
last_day = calendar.monthrange(arg_year, arg_month)[1]  # Nb days in the month
end_date = f"{arg_year}-{arg_month:02d}-{last_day}"

# Get timestamps
start_timestamp = to_timestamp(start_date)
end_timestamp = to_timestamp(end_date) + 86399  # Very last moment of last day

print(f"Fetch balance transactions from {start_date} to {end_date}")

# Fetch balance transactions from given period
balance_transactions = stripe.BalanceTransaction.list(
    created={"gte": start_timestamp, "lte": end_timestamp},
    limit=100,
    expand=['data.source']
)

# Initialize counters: activity is tracked per currency; amounts in
# different currencies are never summed before conversion
payments_by_currency = {}
refunds_by_currency = {}
fees_by_currency = {}
addon_fees_by_currency = {}

# Balance transaction records (populated with --debug)
debug_transactions = []

# Transaction categories
transactions_in_country = []
transactions_in_eu_with_vat = []
transactions_in_eu_without_vat = []
transactions_outside_eu = []
transactions_unknown_country = []
transactions_refunds = []

# Materialize the list once: avoids paging the whole period twice
# (once to count the transactions, once to process them)
transactions = list(balance_transactions.auto_paging_iter())

# Initialize progress counter
progress_count = 0
total_transactions = len(transactions)

print(f"Processing {total_transactions} balance transactions...")

# Charge-details cache shared across the run
charge_details_cache = cache_load("charge_details", CACHE_VERSION)
charge_details_dirty = False

# Process balance transactions
for balance_transaction in transactions:
    progress_count += 1
    sys.stdout.write(f"\rProcessing transaction {progress_count}/{total_transactions}...")
    sys.stdout.flush()

    # Record the transaction for the --debug log
    if args.debug:
        debug_transactions.append({
            "id": balance_transaction.id,
            "type": balance_transaction.type,
            "reporting_category": balance_transaction.reporting_category,
            "amount": balance_transaction.amount / 100,
            "fee": balance_transaction.fee / 100,
            "net": balance_transaction.net / 100,
            "currency": balance_transaction.currency.upper(),
            "included": balance_transaction.type in ['charge', 'payment', 'refund'],
            "date": balance_transaction.created,
            "description": balance_transaction.description or "",
        })

    # Accumulate fees per currency. Stripe's Reports fee total covers all
    # transactions in the period, including fee-only ones (stripe_fee,
    # stripe_fx_fee, tax_fee) and fee credits on refunds, but figures are
    # per currency and must not be mixed.
    bt_currency = balance_transaction.currency.upper()
    fees_by_currency[bt_currency] = fees_by_currency.get(bt_currency, 0) + balance_transaction.fee / 100

    # Add-on product fees (Stripe Billing, Automatic Tax, Radar, Sigma...)
    # are separate balance transactions carrying the cost in their amount
    # with fee=0. Stripe lists them as "Frais supplémentaires Stripe".
    if balance_transaction.reporting_category == 'fee':
        addon_fees_by_currency[bt_currency] = addon_fees_by_currency.get(bt_currency, 0) + balance_transaction.amount / 100

    # Skip non-payment transactions (transfers, adjustments, etc.)
    if balance_transaction.type not in ['charge', 'payment', 'refund']:
        continue

    # Convert amounts from cents to full currency units
    amount = balance_transaction.amount / 100
    fee = balance_transaction.fee / 100
    currency = balance_transaction.currency.upper()

    # Handle refunds separately
    if balance_transaction.type == 'refund':
        refund_stats = refunds_by_currency.setdefault(currency, {"count": 0, "total": 0})
        refund_stats["count"] += 1
        refund_stats["total"] += abs(amount)  # Refunds are negative amounts

        refund_details = {
            "amount": abs(amount),
            "currency": currency,
            "date": balance_transaction.created
        }
        transactions_refunds.append(refund_details)
        continue

    # Process charges (payments)
    payment_stats = payments_by_currency.setdefault(currency, {"count": 0, "total": 0})
    payment_stats["count"] += 1
    payment_stats["total"] += amount

    # Initialize default values
    country = 'Unknown'
    tax_rate_country = None
    billing_country = None
    vat_number = 'Not available'
    vat_applied = False
    has_tax_id = False
    tax_exempt = 'none'
    tax_amount = 0
    customer_email = "No email"
    status = "succeeded"

    # Get source details (charge, payment_intent, etc.)
    source = balance_transaction.source
    charge_id = source.id if source and hasattr(source, "id") else balance_transaction.id

    # Reuse details fetched for this charge on a previous run instead of
    # repeating the same immutable API calls
    details = charge_details_cache.get(charge_id)
    if details is not None:
        customer_email = details["customer_email"]
        billing_country = details["billing_country"]
        tax_rate_country = details["tax_rate_country"]
        vat_number = details["vat_number"]
        vat_applied = details["vat_applied"]
        tax_amount = details["tax_amount"]
        has_tax_id = details.get("has_tax_id", False)
        tax_exempt = details.get("tax_exempt", "none")
    else:
        details_complete = True
        if source and hasattr(source, 'object'):
                try:
                    if source.object == 'charge':
                        # Get customer details from charge
                        if source.customer:
                            # Expanding tax_ids on the existing retrieve call
                            # avoids a separate API call per customer
                            customer = stripe.Customer.retrieve(source.customer, expand=["tax_ids"])
                            customer_email = customer.email or "No email"
                            if customer.address and customer.address.country:
                                billing_country = customer.address.country
                            # A tax-exempt or reverse-charge marking is set
                            # manually by the merchant on the customer, and
                            # only applies to businesses and organizations
                            if customer.tax_exempt:
                                tax_exempt = customer.tax_exempt
                            # Any registered tax ID (EU VAT, US EIN, GB VAT...)
                            # identifies a business
                            if customer.tax_ids and customer.tax_ids.data:
                                has_tax_id = True

                        # Billing address collected at payment time takes
                        # precedence over the saved customer address
                        if source.billing_details and source.billing_details.address and source.billing_details.address.country:
                            billing_country = source.billing_details.address.country

                        # Get payment intent for invoice details via InvoicePayment
                        if source.payment_intent:
                            payment_intent_id = source.payment_intent

                            # Find invoice through InvoicePayment object
                            try:
                                # Search for invoice payments linked to this payment intent
                                invoice_payments = stripe.InvoicePayment.list(
                                    **{
                                        "payment[payment_intent]": payment_intent_id,
                                        "payment[type]": "payment_intent"
                                    },
                                    limit=1
                                )

                                if invoice_payments.data:
                                    invoice_payment = invoice_payments.data[0]
                                    invoice_id = invoice_payment.invoice

                                    # Retrieve the Invoice
                                    invoice = stripe.Invoice.retrieve(invoice_id)

                                    # Extract country from the tax rate used
                                    tax_amounts = invoice.total_taxes or []
                                    for tax in tax_amounts:
                                        if not vat_applied and tax.amount > 0:
                                            vat_applied = True
                                        tax_amount += tax.amount / 100
                                        tax_rate_details = tax.tax_rate_details
                                        if tax_rate_details:
                                            # Retrieve the tax rate details
                                            tax_rate = stripe.TaxRate.retrieve(tax_rate_details.tax_rate)
                                            if tax_rate.country:
                                                tax_rate_country = tax_rate.country

                                    # Extract VAT number if available; any tax
                                    # ID on the invoice also marks the customer
                                    # as a business (extra-EU companies have
                                    # no EU VAT number)
                                    customer_tax_ids = invoice.customer_tax_ids or []
                                    has_tax_id = has_tax_id or bool(customer_tax_ids)
                                    for tax_id in customer_tax_ids:
                                        if tax_id.type == "eu_vat":
                                            vat_number = tax_id.value
                                            break

                            except stripe.error.StripeError as e:
                                print(f"\nError retrieving invoice details for transaction {balance_transaction.id}: {e} - categorized as Unknown")
                                details_complete = False

                except stripe.error.StripeError as e:
                    # Do not skip: the transaction is already counted in the totals,
                    # so it must still land in a category (as Unknown) instead of
                    # silently disappearing from the report
                    print(f"\nError retrieving details for transaction {balance_transaction.id}: {e} - categorized as Unknown")
                    details_complete = False

        if details_complete:
            # Only complete fetches are cached: an interrupted one must be
            # retried on the next run
            charge_details_cache[charge_id] = {
                "customer_email": customer_email,
                "billing_country": billing_country,
                "tax_rate_country": tax_rate_country,
                "vat_number": vat_number,
                "vat_applied": vat_applied,
                "tax_amount": tax_amount,
                "has_tax_id": has_tax_id,
                "tax_exempt": tax_exempt,
            }
            charge_details_dirty = True

    # Classify by the tax rate actually applied on the invoice, so the
    # categories stay consistent with the invoicing (and with what gets
    # declared); fall back to the customer's billing address only when
    # the invoice carries no tax rate. Mismatches between the two are
    # reported as warnings below.
    if tax_rate_country:
        country = tax_rate_country
    elif billing_country:
        country = billing_country

    # Classification warnings: signals that may contradict the category
    warnings = []
    if billing_country and tax_rate_country and billing_country != tax_rate_country:
        warnings.append(
            f"Billing address country {billing_country} does not match tax rate country {tax_rate_country}"
        )
    if vat_number != "Not available":
        vat_prefix = re.match(r"([A-Za-z]{2})", vat_number)
        if vat_prefix and vat_prefix.group(1).upper() != country:
            warnings.append(f"VAT number prefix {vat_prefix.group(1).upper()} does not match country {country}")
    if country in EU_COUNTRIES and not vat_applied and vat_number == "Not available":
        warnings.append("Reverse-charged EU sale without a customer VAT number")

    # B2B detection: a VAT number is only one signal, and extra-EU businesses
    # never have one. A transaction is B2B when any of these hold:
    # - an EU VAT number is present on the invoice
    # - the customer has any registered tax ID (US EIN, GB VAT, ...)
    # - the merchant marked the customer tax-exempt or reverse-charge
    # - the sale is intra-EU with no VAT applied (reverse charge is B2B
    #   by definition, even when the VAT number was not captured)
    # Businesses without any of these signals (e.g. below a domestic VAT
    # registration threshold) fall back to B2C.
    is_b2b = (
        vat_number != "Not available"
        or has_tax_id
        or tax_exempt in ("exempt", "reverse")
        or (country in EU_COUNTRIES and country != arg_country and not vat_applied)
    )

    # Transaction details dictionary
    transaction_details = {
        "date": balance_transaction.created,
        "status": status,
        "amount": amount,
        "currency": currency,
        "email": customer_email,
        "country": country,
        "vat_number": vat_number,
        "vat_applied": vat_applied,
        "tax_country": tax_rate_country,
        "tax_amount": tax_amount,
        "b2b": is_b2b,
        "fee": fee,
        "warnings": warnings,
    }

    # Categorize transaction
    if country == arg_country:
        transactions_in_country.append(transaction_details)
    elif country in EU_COUNTRIES:
        if vat_applied:
            transactions_in_eu_with_vat.append(transaction_details)
        else:
            transactions_in_eu_without_vat.append(transaction_details)
    elif country == "Unknown":
        transactions_unknown_country.append(transaction_details)
    else:
        transactions_outside_eu.append(transaction_details)

# Clear progress indicator
sys.stdout.write('\r' + ' ' * 50 + '\r')
sys.stdout.flush()
print("Processing completed!")

# Persist newly fetched charge details for the next runs
if charge_details_dirty:
    cache_save("charge_details", CACHE_VERSION, charge_details_cache)

# Currencies present in the report: every currency with payments,
# refunds or fees, plus the default currency itself
currencies = sorted(
    {t['currency'] for t in (
        transactions_in_country + transactions_in_eu_with_vat + transactions_in_eu_without_vat +
        transactions_outside_eu + transactions_unknown_country + transactions_refunds
    )} |
    set(fees_by_currency) |
    set(payments_by_currency) | set(refunds_by_currency) |
    {arg_currency}
)

# Official conversion rates for the report period (douane.gouv.fr monthly
# rates, in the "1 EUR = X CUR" direction). The rate applicable on the
# period start date covers the whole month.
print(f"\nResolving exchange rates for {start_date} (douane.gouv.fr)...")
rates, rate_errors = get_rates(currencies, start_date, fx_overrides)
for currency in sorted(rates):
    if currency == "EUR":
        continue
    source = "manual override" if currency in fx_overrides else "douane.gouv.fr"
    print(f"  {currency}: 1 EUR = {rates[currency]} {currency} ({source})")
for currency in sorted(rate_errors):
    print(f"  Warning: no rate for {currency}: {rate_errors[currency]}")

if rate_errors:
    print(
        "\nWarning: amounts in currencies without a rate are reported in their "
        "original currency only and are NOT included in converted totals."
    )


def convert(amount, currency):
    """Convert an amount into the default report currency using the
    official rate (amount CUR -> amount / rate(CUR) EUR -> * rate(default)).
    Returns None when no rate is available; original amounts are never
    silently mixed."""
    rate_from = rates.get(currency)
    rate_to = rates.get(arg_currency)
    if rate_from is None or rate_to is None:
        return None
    return float(
        (Decimal(str(amount)) * rate_to / rate_from).quantize(Decimal("0.01"), ROUND_HALF_UP)
    )


def converted_sum(transactions, field="amount"):
    """Sum a field over transactions, converted to the default currency.
    Returns None if any transaction cannot be converted (no rate)."""
    total = Decimal(0)
    for t in transactions:
        converted = convert(t[field], t['currency'])
        if converted is None:
            return None
        total += Decimal(str(converted))
    return float(total)


# Per-currency activity stats for the report cards
currency_stats = {}
for currency in currencies:
    payments = payments_by_currency.get(currency, {"count": 0, "total": 0})
    refunds = refunds_by_currency.get(currency, {"count": 0, "total": 0})
    currency_stats[currency] = {
        "payments": payments["count"],
        "payments_total": payments["total"],
        "refunds": refunds["count"],
        "refunds_total": refunds["total"],
        "fees": fees_by_currency.get(currency, 0),
        "addon_fees": addon_fees_by_currency.get(currency, 0),
    }

if args.debug:
    skipped = [t for t in debug_transactions if not t['included']]
    skipped_amount = sum(t['amount'] for t in skipped)
    skipped_fee = sum(t['fee'] for t in skipped)

    debug_filename = f"debug_{start_date.replace('-', '')}_{end_date.replace('-', '')}.txt"
    with open(debug_filename, 'w', encoding='utf-8') as debugfile:
        debugfile.write(f"Lavender Report debug log - {start_date} to {end_date}\n")
        debugfile.write(f"Window (Europe/Paris): epoch {start_timestamp} to {end_timestamp}\n\n")
        debugfile.write(
            f"{'included':<9} {'id':<32} {'type':<28} {'category':<28} "
            f"{'currency':<9} {'amount':>10} {'fee':>10} {'net':>10}  date\n"
        )
        for t in debug_transactions:
            debugfile.write(
                f"{'yes' if t['included'] else 'no':<9} {t['id']:<32} {t['type']:<28} "
                f"{t['reporting_category']:<28} {t['currency']:<9} "
                f"{t['amount']:>10.2f} {t['fee']:>10.2f} {t['net']:>10.2f}  "
                f"{datetime.fromtimestamp(t['date'], pytz.utc).strftime('%Y-%m-%d %H:%M:%S')} | {t['description']}\n"
            )
        debugfile.write("\nTotals:\n")
        for cur in currencies:
            payments = payments_by_currency.get(cur, {"count": 0, "total": 0})
            refunds = refunds_by_currency.get(cur, {"count": 0, "total": 0})
            debugfile.write(f"  Payments ({cur}): {payments['count']} | {payments['total']:.2f}\n")
            debugfile.write(f"  Refunds ({cur}): {refunds['count']} | {refunds['total']:.2f}\n")
        fees_lines = ", ".join(f"{cur}={fees_by_currency[cur]:.2f}" for cur in sorted(fees_by_currency))
        debugfile.write(f"  Fees per currency: {fees_lines}\n")
        if addon_fees_by_currency:
            addon_lines = ", ".join(f"{cur}={addon_fees_by_currency[cur]:.2f}" for cur in sorted(addon_fees_by_currency))
            debugfile.write(f"  Add-on fees per currency: {addon_lines}\n")
        debugfile.write(f"  Skipped: {len(skipped)} | amount={skipped_amount:.2f} | fee={skipped_fee:.2f}\n")
        debugfile.write("  Skipped transactions detail:\n")
        for t in skipped:
            debugfile.write(
                f"    {t['id']} | type: {t['type']} | category: {t['reporting_category']} | "
                f"currency: {t['currency']} | amount: {t['amount']:.2f} | fee: {t['fee']:.2f} | net: {t['net']:.2f} | "
                f"{datetime.fromtimestamp(t['date'], pytz.utc).strftime('%Y-%m-%d %H:%M:%S')} | {t['description']}\n"
            )
    print(f"\nDebug log written to {debug_filename}")

# Summary
print("\nSummary (per currency; never mixed before conversion):")
for currency in currencies:
    stats = currency_stats[currency]
    print(f"  {currency}: {stats['payments']} payments | {format_amount(stats['payments_total'], currency)}"
          f" | {stats['refunds']} refunds | {format_amount(stats['refunds_total'], currency)}"
          f" | Fees: {format_amount(stats['fees'], currency)}")

# Whole situation: combined activity converted to the default currency
# at the official rates
combined_payments = converted_sum(
    [t for cat in (
        transactions_in_country, transactions_in_eu_with_vat, transactions_in_eu_without_vat,
        transactions_outside_eu, transactions_unknown_country
    ) for t in cat]
)
combined_refunds = converted_sum(transactions_refunds)
fees_parts = [convert(currency_stats[c]['fees'], c) for c in currencies]
combined_fees = sum(fees_parts) if all(p is not None for p in fees_parts) else None
if combined_payments is not None:
    print(f"\nWhole situation (converted to {arg_currency} at the official rates):")
    print(f"  Total payments: {format_amount(combined_payments, arg_currency)}")
    if combined_refunds is not None:
        print(f"  Total refunds: {format_amount(combined_refunds, arg_currency)}")
        print(f"  Net total: {format_amount(combined_payments - combined_refunds, arg_currency)}")
    if combined_fees is not None:
        print(f"  Total Stripe fees: {format_amount(combined_fees, arg_currency)}")

# French VAT declaration: B2C sales charged French VAT (domestic and EU
# consumers, mirroring the "particuliers UE avec TVA francaise" line) and
# the domestic B2C/B2B split on HT bases. Figures are converted to the
# default currency at the official monthly rates: foreign-currency sales
# are declared converted, never in their original currency.
french_b2c_txns = [
    t for t in transactions_in_country + transactions_in_eu_with_vat
    if t["tax_country"] == arg_country and t["vat_applied"] and not t["b2b"]
]
french_b2c_ht_sum = converted_sum([
    {"amount": ht_amount(t), "currency": t["currency"]} for t in french_b2c_txns
])
french_b2c_tva_sum = converted_sum([
    {"amount": t["tax_amount"], "currency": t["currency"]} for t in french_b2c_txns
])
domestic_b2c_txns = [t for t in transactions_in_country if not t["b2b"]]
domestic_b2b_txns = [t for t in transactions_in_country if t["b2b"]]
domestic_b2c_ht_sum = converted_sum([
    {"amount": ht_amount(t), "currency": t["currency"]} for t in domestic_b2c_txns
])
domestic_b2b_ht_sum = converted_sum([
    {"amount": ht_amount(t), "currency": t["currency"]} for t in domestic_b2b_txns
])

print("\nFrench VAT declaration (all currencies converted to {}):".format(arg_currency))
if french_b2c_ht_sum is not None:
    print(f"  EU consumers with French VAT (HT): {format_amount(french_b2c_ht_sum, arg_currency)} | TVA collected: {format_amount(french_b2c_tva_sum, arg_currency)}")
else:
    print("  EU consumers with French VAT (HT): no rate available for some currencies, see warnings above")
if domestic_b2c_ht_sum is not None:
    print(f"  Domestic B2C (HT): {format_amount(domestic_b2c_ht_sum, arg_currency)}")
if domestic_b2b_ht_sum is not None:
    print(f"  Domestic B2B (HT): {format_amount(domestic_b2b_ht_sum, arg_currency)}")

# Classification warnings: possible mismatches to review before declaring
all_categorized_transactions = (
    transactions_in_country + transactions_in_eu_with_vat + transactions_in_eu_without_vat +
    transactions_outside_eu + transactions_unknown_country
)
warned_transactions = [t for t in all_categorized_transactions if t["warnings"]]
if warned_transactions:
    print(f"\nWarnings ({len(warned_transactions)}) - possible classification mismatches, review before declaring:")
    for t in warned_transactions:
        print(
            f"  {datetime.fromtimestamp(t['date'], pytz.utc).strftime('%Y-%m-%d %H:%M:%S')} | "
            f"{format_amount(t['amount'], t['currency'])} | {t['country']} | {t['email']} | {'; '.join(t['warnings'])}"
        )

# Function to print details for each transaction
def print_transaction_details(transactions, category_name):
    totals_by_currency = {}
    for t in transactions:
        totals_by_currency[t['currency']] = totals_by_currency.get(t['currency'], 0) + t['amount']
    totals_text = " | ".join(
        format_amount(totals_by_currency[cur], cur) for cur in sorted(totals_by_currency)
    ) or format_amount(0, arg_currency)
    print(f"\n{category_name}: {len(transactions)} | Total: {totals_text}")
    for i, t in enumerate(transactions, start=1):
        # Rounded amount for the declaration: whole-unit rounding in the
        # default currency, of the converted amount for non-default ones
        converted = convert(t['amount'], t['currency'])
        if converted is None:
            rounded_text = "Rounded: N/A (no rate)"
        else:
            rounded_amount = int(Decimal(str(converted)).quantize(0, ROUND_HALF_UP))
            rounded_text = f"Rounded: {format_amount(rounded_amount, arg_currency)}"
        print(
            f" {i}. Amount: {format_amount(t['amount'], t['currency'])} "
            f"({rounded_text}) "
            f"- TVA: {t['vat_number']} - Country: {t['country']} "
            f"- Date: {datetime.fromtimestamp(t['date'], pytz.utc).strftime('%Y-%m-%d %H:%M:%S')} "
            f"- Email: {t['email']} - Status: {t['status']} "
            f"- Fees: {format_amount(t['fee'], t['currency'])}"
        )


def format_date(timestamp):
    """Format timestamp to readable date string."""
    return datetime.fromtimestamp(timestamp, pytz.utc).strftime('%Y-%m-%d %H:%M:%S')


# Export functionality
if export_format:
    if not output_filename:
        # Generate default filename
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        if export_format == 'csv':
            output_filename = f'lavender_report_{start_date.replace("-", "")}_to_{end_date.replace("-", "")}_{timestamp}.csv'
        else:  # html
            output_filename = f'lavender_report_{start_date.replace("-", "")}_to_{end_date.replace("-", "")}_{timestamp}.html'
    
    print(f"\nExporting {export_format.upper()} report to {output_filename}...")
    
    if export_format == 'csv':
        csv_data = generate_csv_report(
            transactions_in_country, transactions_in_eu_with_vat, transactions_in_eu_without_vat,
            transactions_outside_eu, transactions_unknown_country, transactions_refunds,
            arg_country, arg_currency, format_date, convert, currency_stats, rates, start_date, set(fx_overrides)
        )
        with open(output_filename, 'w', newline='', encoding='utf-8') as csvfile:
            writer = csv.writer(csvfile)
            writer.writerows(csv_data)
        print(f"CSV report exported successfully to {output_filename}")
    
    elif export_format == 'html':
        html_content = generate_html_report(
            transactions_in_country, transactions_in_eu_with_vat, transactions_in_eu_without_vat,
            transactions_outside_eu, transactions_unknown_country, transactions_refunds,
            arg_country, start_date, end_date, arg_currency, format_amount, format_date, ht_amount,
            convert, currency_stats, rates, set(fx_overrides)
        )
        with open(output_filename, 'w', encoding='utf-8') as htmlfile:
            htmlfile.write(html_content)
        print(f"HTML report exported successfully to {output_filename}")


# Payments
print_transaction_details([t for t in transactions_in_country if not t['b2b']], "Domestic B2C transactions (your company's country)")
print_transaction_details([t for t in transactions_in_country if t['b2b']], "Domestic B2B transactions (your company's country)")
print_transaction_details(transactions_in_eu_with_vat, "Intra-EU transactions (with VAT)")
print_transaction_details(transactions_in_eu_without_vat, "Intra-EU transactions (with reverse-charged VAT)")
print_transaction_details(transactions_outside_eu, "Extra-EU transactions")
print_transaction_details(transactions_unknown_country, "Unknown transactions")

# Refunds
refund_totals_by_currency = {}
for t in transactions_refunds:
    refund_totals_by_currency[t['currency']] = refund_totals_by_currency.get(t['currency'], 0) + t['amount']
refund_totals_text = " | ".join(
    format_amount(refund_totals_by_currency[cur], cur) for cur in sorted(refund_totals_by_currency)
) or format_amount(0, arg_currency)
print(f"\nRefunded transactions: {len(transactions_refunds)} | Total: {refund_totals_text}")
for i, t in enumerate(transactions_refunds, start=1):
    print(f"  {i}. Amount: {format_amount(t['amount'], t['currency'])} - Date: {datetime.fromtimestamp(t['date'], pytz.utc).strftime('%Y-%m-%d %H:%M:%S')}")
