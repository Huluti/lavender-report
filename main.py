from datetime import datetime
from dotenv import load_dotenv
import stripe
import pytz
import os
import sys
import calendar
import argparse
import csv
import html
import json
import re
from decimal import Decimal, ROUND_HALF_UP

load_dotenv()

# Stripe secret key
stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
if stripe.api_key is None:
    print("Error: STRIPE_SECRET_KEY environment variable not set.", file=sys.stderr)
    sys.exit(1)

# Parse arguments
now = datetime.now()
current_year = now.year
last_month = now.month - 1 or 12  # If month is January (1), last month should be December (12)

parser = argparse.ArgumentParser(description="Lavender Report")
parser.add_argument('--country', type=str, help="Country", default="FR")
parser.add_argument('--year', type=int, help="Year", default=current_year)
parser.add_argument('--month', type=int, help="Month", default=last_month)
parser.add_argument('--export', type=str, choices=['csv', 'html'], help="Export format (csv or html)")
parser.add_argument('--output', type=str, help="Output filename for export")
parser.add_argument('--debug', action='store_true', help="Log balance transactions skipped by the type filter")
parser.add_argument('--currency', type=str, help="Report currency (default: EUR). Transactions in other currencies are listed separately", default="EUR")
parser.add_argument('--locale', type=str, choices=['en', 'fr'], help="Number formatting for display (en: 3571.65 EUR, fr: 3 571,65 EUR with euro sign)", default="en")
args = parser.parse_args()

arg_country = args.country
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
CACHE_VERSION = 2
CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".cache")
CACHE_FILE = os.path.join(CACHE_DIR, "charge_details.json")


def load_charge_details_cache():
    try:
        with open(CACHE_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    if data.get("version") != CACHE_VERSION:
        return {}
    return data.get("charges", {})


def save_charge_details_cache(cache):
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump({"version": CACHE_VERSION, "charges": cache}, f)


def country_flag(country):
    """Flag emoji for an ISO country code, empty string otherwise."""
    if len(country) == 2 and country.isalpha() and country.isupper():
        return chr(0x1F1E6 + ord(country[0]) - ord('A')) + chr(0x1F1E6 + ord(country[1]) - ord('A'))
    return ""


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

# Initialize counters
nb_payments = 0
nb_refunds = 0
total_payments = 0
total_refunds = 0
total_fees = 0
fees_by_currency = {}
addon_fees_by_currency = {}
other_currency_transactions = []

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
charge_details_cache = load_charge_details_cache()
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

    # Track transactions outside the report currency separately: mixing
    # currencies into a single total inflates it (USD amounts summed as EUR)
    if currency != arg_currency:
        other_currency_transactions.append({
            "type": balance_transaction.type,
            "amount": amount,
            "fee": fee,
            "currency": currency,
        })
        continue

    # Handle refunds separately
    if balance_transaction.type == 'refund':
        total_refunds += abs(amount)  # Refunds are negative amounts
        nb_refunds += 1

        refund_details = {
            "amount": abs(amount),
            "currency": currency,
            "date": balance_transaction.created
        }
        transactions_refunds.append(refund_details)
        continue

    # Process charges (payments)
    nb_payments += 1
    total_payments += amount

    # Initialize default values
    country = 'Unknown'
    tax_rate_country = None
    billing_country = None
    vat_number = 'Not available'
    vat_applied = False
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
    else:
        details_complete = True
        if source and hasattr(source, 'object'):
                try:
                    if source.object == 'charge':
                        # Get customer details from charge
                        if source.customer:
                            customer = stripe.Customer.retrieve(source.customer)
                            customer_email = customer.email or "No email"
                            if customer.address and customer.address.country:
                                billing_country = customer.address.country

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

                                    # Extract VAT number if available
                                    customer_tax_ids = invoice.customer_tax_ids or []
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
        "b2b": vat_number != "Not available",
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
    save_charge_details_cache(charge_details_cache)

# Fees for the report currency; other currencies stay in fees_by_currency
total_fees = fees_by_currency.get(arg_currency, 0)
addon_fees = addon_fees_by_currency.get(arg_currency, 0)

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
        debugfile.write(f"  Payments ({arg_currency}): {nb_payments} | {total_payments:.2f}\n")
        debugfile.write(f"  Refunds ({arg_currency}): {nb_refunds} | {total_refunds:.2f}\n")
        fees_lines = ", ".join(f"{cur}={fees_by_currency[cur]:.2f}" for cur in sorted(fees_by_currency))
        debugfile.write(f"  Fees per currency: {fees_lines}\n")
        if addon_fees_by_currency:
            addon_lines = ", ".join(f"{cur}={addon_fees_by_currency[cur]:.2f}" for cur in sorted(addon_fees_by_currency))
            debugfile.write(f"  Add-on fees per currency: {addon_lines}\n")
        debugfile.write(f"  Skipped: {len(skipped)} | amount={skipped_amount:.2f} | fee={skipped_fee:.2f}\n")
        debugfile.write(f"  Skipped transactions detail:\n")
        for t in skipped:
            debugfile.write(
                f"    {t['id']} | type: {t['type']} | category: {t['reporting_category']} | "
                f"currency: {t['currency']} | amount: {t['amount']:.2f} | fee: {t['fee']:.2f} | net: {t['net']:.2f} | "
                f"{datetime.fromtimestamp(t['date'], pytz.utc).strftime('%Y-%m-%d %H:%M:%S')} | {t['description']}\n"
            )
    print(f"\nDebug log written to {debug_filename}")

# Summary
print("\nSummary:")
print(f"Number of payments: {nb_payments}")
print(f"Total: {format_amount(total_payments, arg_currency)}")
print(f"Total Stripe fees: {format_amount(total_fees, arg_currency)}")
if addon_fees:
    print(f"Add-on Stripe fees (Billing, Automatic Tax, Radar, Sigma...): {format_amount(addon_fees, arg_currency)}")
    print(f"Total Stripe fees incl. add-ons: {format_amount(total_fees - addon_fees, arg_currency)}")

if other_currency_transactions:
    print("\nTransactions in other currencies (excluded from the totals above):")
    for cur in sorted({t['currency'] for t in other_currency_transactions}):
        cur_txns = [t for t in other_currency_transactions if t['currency'] == cur]
        cur_amount = sum(t['amount'] for t in cur_txns)
        cur_fee = sum(t['fee'] for t in cur_txns)
        print(f"  {cur}: {len(cur_txns)} transactions | Amount: {format_amount(cur_amount, cur)} | Fees: {format_amount(cur_fee, cur)}")

# French VAT declaration: B2C sales charged French VAT (domestic and EU
# consumers, mirroring the "particuliers UE avec TVA francaise" line) and
# the domestic B2C/B2B split on HT bases
french_b2c_txns = [
    t for t in transactions_in_country + transactions_in_eu_with_vat
    if t["tax_country"] == arg_country and t["vat_applied"] and not t["b2b"]
]
french_b2c_ht = sum(ht_amount(t) for t in french_b2c_txns)
french_b2c_tva = sum(t["tax_amount"] for t in french_b2c_txns)
domestic_b2c_ht = sum(ht_amount(t) for t in transactions_in_country if not t["b2b"])
domestic_b2b_ht = sum(ht_amount(t) for t in transactions_in_country if t["b2b"])
print("\nFrench VAT declaration:")
print(f"  EU consumers with French VAT (HT): {format_amount(french_b2c_ht, arg_currency)} | TVA collected: {format_amount(french_b2c_tva, arg_currency)}")
print(f"  Domestic B2C (HT): {format_amount(domestic_b2c_ht, arg_currency)}")
print(f"  Domestic B2B (HT): {format_amount(domestic_b2b_ht, arg_currency)}")

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
    print(f"\n{category_name}: {len(transactions)} | Total: {format_amount(sum(t['amount'] for t in transactions), arg_currency)}")
    for i, t in enumerate(transactions, start=1):
        rounded_amount = int(Decimal(str(t['amount'])).quantize(0, ROUND_HALF_UP))
        print(
            f" {i}. Amount: {format_amount(t['amount'], t['currency'])} "
            f"(Rounded: {format_amount(rounded_amount, t['currency'])}) "
            f"- TVA: {t['vat_number']} - Country: {t['country']} "
            f"- Date: {datetime.fromtimestamp(t['date'], pytz.utc).strftime('%Y-%m-%d %H:%M:%S')} "
            f"- Email: {t['email']} - Status: {t['status']} "
            f"- Fees: {format_amount(t['fee'], t['currency'])}"
        )


def format_date(timestamp):
    """Format timestamp to readable date string."""
    return datetime.fromtimestamp(timestamp, pytz.utc).strftime('%Y-%m-%d %H:%M:%S')


def generate_category_section(transactions, title):
    """Generate the HTML section for one category of transactions."""
    if not transactions:
        return ""
    total = sum(t['amount'] for t in transactions)
    rows = ""
    for i, t in enumerate(transactions, start=1):
        rounded_amount = int(Decimal(str(t['amount'])).quantize(0, ROUND_HALF_UP))
        vat_badge = "Yes" if t['vat_applied'] else "No"
        if t['warnings']:
            warning_prefix = f'<span title="{html.escape("; ".join(t["warnings"]))}">⚠️ </span>'
        else:
            warning_prefix = ""
        rows += f'''
                <tr>
                    <td>{warning_prefix}{i}</td>
                    <td>{format_date(t['date'])}</td>
                    <td class="amount-positive">{format_amount(t['amount'], t['currency'])}</td>
                    <td>{format_amount(rounded_amount, t['currency'])}</td>
                    <td>{country_flag(t['country'])} {t['country']}</td>
                    <td>{html.escape(t['vat_number'])}</td>
                    <td>{vat_badge}</td>
                    <td>{'B2B' if t['b2b'] else 'B2C'}</td>
                    <td>{html.escape(t['email'])}</td>
                    <td>{t['status']}</td>
                    <td>{format_amount(t['fee'], t['currency'])}</td>
                </tr>
'''
    return f'''
    <div class="category-section">
        <div class="category-title">
            {title} - {len(transactions)} transactions | Total: {format_amount(total, arg_currency)}
        </div>
        <table>
            <thead>
                <tr>
                    <th>#</th>
                    <th>Date</th>
                    <th>Amount</th>
                    <th>Rounded</th>
                    <th>Country</th>
                    <th>VAT Number</th>
                    <th>VAT Applied</th>
                    <th>Type</th>
                    <th>Email</th>
                    <th>Status</th>
                    <th>Fees</th>
                </tr>
            </thead>
            <tbody>{rows}
            </tbody>
        </table>
    </div>
'''


def generate_csv_report(
    transactions_in_country, transactions_in_eu_with_vat, transactions_in_eu_without_vat,
    transactions_outside_eu, transactions_unknown_country, transactions_refunds,
    nb_payments, total_payments, total_fees, addon_fees, nb_refunds, total_refunds,
    arg_country
):
    """Generate CSV report of all transactions."""
    output = []
    
    # Write header
    output.append([
        "Date", "Type", "Amount", "Currency", "Rounded Amount", "Country", 
        "VAT Number", "VAT Applied", "Email", "Status", "Fees", "Category"
    ])
    
    # Add domestic transactions
    domestic_total = sum(t['amount'] for t in transactions_in_country)
    for t in transactions_in_country:
        rounded_amount = int(Decimal(str(t['amount'])).quantize(0, ROUND_HALF_UP))
        output.append([
            format_date(t['date']),
            "Payment",
            f"{t['amount']:.2f}",
            t['currency'],
            f"{rounded_amount}",
            t['country'],
            t['vat_number'],
            "Yes" if t['vat_applied'] else "No",
            t['email'],
            t['status'],
            f"{t['fee']:.2f} {t['currency']}",
            f"Domestic ({arg_country})"
        ])
    output.append([
        f"Domestic ({arg_country}) Total", "", f"{domestic_total:.2f} EUR", "", "", 
        "", "", "", "", "", "", ""
    ])
    
    # Add EU with VAT transactions
    eu_vat_total = sum(t['amount'] for t in transactions_in_eu_with_vat)
    for t in transactions_in_eu_with_vat:
        rounded_amount = int(Decimal(str(t['amount'])).quantize(0, ROUND_HALF_UP))
        output.append([
            format_date(t['date']),
            "Payment",
            f"{t['amount']:.2f}",
            t['currency'],
            f"{rounded_amount}",
            t['country'],
            t['vat_number'],
            "Yes",
            t['email'],
            t['status'],
            f"{t['fee']:.2f} {t['currency']}",
            "Intra-EU (with VAT)"
        ])
    output.append([
        "Intra-EU (with VAT) Total", "", f"{eu_vat_total:.2f} EUR", "", "", 
        "", "", "", "", "", "", ""
    ])
    
    # Add EU without VAT transactions
    eu_no_vat_total = sum(t['amount'] for t in transactions_in_eu_without_vat)
    for t in transactions_in_eu_without_vat:
        rounded_amount = int(Decimal(str(t['amount'])).quantize(0, ROUND_HALF_UP))
        output.append([
            format_date(t['date']),
            "Payment",
            f"{t['amount']:.2f}",
            t['currency'],
            f"{rounded_amount}",
            t['country'],
            t['vat_number'],
            "No",
            t['email'],
            t['status'],
            f"{t['fee']:.2f} {t['currency']}",
            "Intra-EU (reverse-charged VAT)"
        ])
    output.append([
        "Intra-EU (reverse-charged VAT) Total", "", f"{eu_no_vat_total:.2f} EUR", "", "", 
        "", "", "", "", "", "", ""
    ])
    
    # Add extra-EU transactions
    extra_eu_total = sum(t['amount'] for t in transactions_outside_eu)
    for t in transactions_outside_eu:
        rounded_amount = int(Decimal(str(t['amount'])).quantize(0, ROUND_HALF_UP))
        output.append([
            format_date(t['date']),
            "Payment",
            f"{t['amount']:.2f}",
            t['currency'],
            f"{rounded_amount}",
            t['country'],
            t['vat_number'],
            "No" if not t['vat_applied'] else "Yes",
            t['email'],
            t['status'],
            f"{t['fee']:.2f} {t['currency']}",
            "Extra-EU"
        ])
    output.append([
        "Extra-EU Total", "", f"{extra_eu_total:.2f} EUR", "", "", 
        "", "", "", "", "", "", ""
    ])
    
    # Add unknown country transactions
    unknown_total = sum(t['amount'] for t in transactions_unknown_country)
    for t in transactions_unknown_country:
        rounded_amount = int(Decimal(str(t['amount'])).quantize(0, ROUND_HALF_UP))
        output.append([
            format_date(t['date']),
            "Payment",
            f"{t['amount']:.2f}",
            t['currency'],
            f"{rounded_amount}",
            t['country'],
            t['vat_number'],
            "No" if not t['vat_applied'] else "Yes",
            t['email'],
            t['status'],
            f"{t['fee']:.2f} {t['currency']}",
            "Unknown"
        ])
    output.append([
        "Unknown Total", "", f"{unknown_total:.2f} EUR", "", "", 
        "", "", "", "", "", "", ""
    ])
    
    # Add refunds
    for t in transactions_refunds:
        output.append([
            format_date(t['date']),
            "Refund",
            f"{t['amount']:.2f}",
            t['currency'],
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "Refund"
        ])
    
    # Add summary row
    output.append([])
    output.append(["SUMMARY"])
    output.append(["Total Payments", f"{nb_payments}"])
    output.append(["Total Payment Amount", f"{total_payments:.2f} EUR"])
    output.append(["Total Stripe Fees", f"{total_fees:.2f} EUR"])
    if addon_fees:
        output.append(["Add-on Stripe Fees (Billing, Automatic Tax, Radar, Sigma...)", f"{addon_fees:.2f} EUR"])
        output.append(["Total Stripe Fees incl. add-ons", f"{total_fees - addon_fees:.2f} EUR"])
    output.append(["Total Refunds", f"{nb_refunds}"])
    output.append(["Total Refund Amount", f"{total_refunds:.2f} EUR"])
    output.append(["Net Total", f"{total_payments - total_refunds:.2f} EUR"])

    # Classification warnings
    warned = [t for t in (
        transactions_in_country + transactions_in_eu_with_vat + transactions_in_eu_without_vat +
        transactions_outside_eu + transactions_unknown_country
    ) if t["warnings"]]
    if warned:
        output.append([])
        output.append([f"WARNINGS ({len(warned)}) - possible classification mismatches, review before declaring"])
        for t in warned:
            output.append([
                format_date(t['date']),
                "Payment",
                f"{t['amount']:.2f}",
                t['currency'],
                "",
                t['country'],
                t['vat_number'],
                "",
                t['email'],
                t['status'],
                "",
                "; ".join(t['warnings'])
            ])

    return output


def generate_html_report(
    transactions_in_country, transactions_in_eu_with_vat, transactions_in_eu_without_vat,
    transactions_outside_eu, transactions_unknown_country, transactions_refunds,
    nb_payments, total_payments, total_fees, addon_fees, nb_refunds, total_refunds,
    arg_country, start_date, end_date
):
    """Generate HTML report of all transactions."""
    # Fee cards: add-on fees only appear when they exist
    fee_cards = f'''
            <div class="summary-item">
                <strong>Total Stripe Fees</strong>
                <span class="summary-value">{format_amount(total_fees, arg_currency)}</span>
            </div>'''
    if addon_fees:
        fee_cards += f'''
            <div class="summary-item">
                <strong>Add-on Stripe Fees</strong>
                <span class="summary-value">{format_amount(addon_fees, arg_currency)}</span>
            </div>
            <div class="summary-item">
                <strong>Total Stripe Fees incl. add-ons</strong>
                <span class="summary-value">{format_amount(total_fees - addon_fees, arg_currency)}</span>
            </div>'''

    # VAT category cards: empty categories are omitted
    vat_cards = f'''
            <div class="summary-item">
                <strong>Domestic ({arg_country})</strong>
                <span class="summary-value">{format_amount(sum(t['amount'] for t in transactions_in_country), arg_currency)}</span>
            </div>'''
    if any(not t['b2b'] for t in transactions_in_country):
        vat_cards += f'''
            <div class="summary-item">
                <strong>Domestic B2C (HT)</strong>
                <span class="summary-value">{format_amount(sum(ht_amount(t) for t in transactions_in_country if not t['b2b']), arg_currency)}</span>
            </div>'''
    if any(t['b2b'] for t in transactions_in_country):
        vat_cards += f'''
            <div class="summary-item">
                <strong>Domestic B2B (HT)</strong>
                <span class="summary-value">{format_amount(sum(ht_amount(t) for t in transactions_in_country if t['b2b']), arg_currency)}</span>
            </div>'''
    if transactions_in_eu_with_vat:
        vat_cards += f'''
            <div class="summary-item">
                <strong>Intra-EU (with VAT)</strong>
                <span class="summary-value">{format_amount(sum(t['amount'] for t in transactions_in_eu_with_vat), arg_currency)}</span>
            </div>'''
    vat_cards += f'''
            <div class="summary-item">
                <strong>Intra-EU (reverse-charged)</strong>
                <span class="summary-value">{format_amount(sum(t['amount'] for t in transactions_in_eu_without_vat), arg_currency)}</span>
            </div>
            <div class="summary-item">
                <strong>Extra-EU</strong>
                <span class="summary-value">{format_amount(sum(t['amount'] for t in transactions_outside_eu), arg_currency)}</span>
            </div>'''
    if transactions_unknown_country:
        vat_cards += f'''
            <div class="summary-item">
                <strong>Unknown</strong>
                <span class="summary-value">{format_amount(sum(t['amount'] for t in transactions_unknown_country), arg_currency)}</span>
            </div>'''

    # French VAT declaration group: B2C sales charged French VAT
    french_vat_group = ""
    french_b2c_txns = [
        t for t in transactions_in_country + transactions_in_eu_with_vat
        if t["tax_country"] == arg_country and t["vat_applied"] and not t["b2b"]
    ]
    if french_b2c_txns:
        french_b2c_ht = sum(ht_amount(t) for t in french_b2c_txns)
        french_b2c_tva = sum(t["tax_amount"] for t in french_b2c_txns)
        french_vat_group = f'''
        <div class="summary-group">
            <div class="summary-group-label">French VAT declaration</div>
            <div class="summary-grid">
            <div class="summary-item">
                <strong>EU consumers, French VAT (HT)</strong>
                <span class="summary-value">{format_amount(french_b2c_ht, arg_currency)}</span>
            </div>
            <div class="summary-item">
                <strong>TVA collected (B2C)</strong>
                <span class="summary-value">{format_amount(french_b2c_tva, arg_currency)}</span>
            </div>
            </div>
        </div>'''

    html_content = f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Lavender Report - {start_date} to {end_date}</title>
    <style>
        body {{
            font-family: Arial, sans-serif;
            margin: 20px;
            color: #333;
        }}
        h1 {{
            color: #635bff;
            border-bottom: 2px solid #635bff;
            padding-bottom: 10px;
        }}
        h2 {{
            color: #555;
            margin-top: 20px;
            border-bottom: 1px solid #ddd;
            padding-bottom: 5px;
        }}
        .summary {{
            background-color: #f8f9fa;
            padding: 15px;
            border-radius: 8px;
            margin-bottom: 20px;
        }}
        .summary-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 15px;
        }}
        .summary-group {{
            margin-bottom: 20px;
        }}
        .summary-group:last-child {{
            margin-bottom: 0;
        }}
        .summary-group-label {{
            font-size: 0.8em;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            color: #8898aa;
            margin-bottom: 8px;
        }}
        .summary-item {{
            background-color: white;
            padding: 10px;
            border-radius: 4px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }}
        .summary-item strong {{
            display: block;
            color: #666;
            font-size: 0.9em;
        }}
        .summary-value {{
            font-size: 1.2em;
            color: #333;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            margin-bottom: 20px;
        }}
        th, td {{
            padding: 10px;
            text-align: left;
            border-bottom: 1px solid #ddd;
        }}
        th {{
            background-color: #635bff;
            color: white;
            font-weight: 600;
        }}
        tr:hover {{
            background-color: #f5f5f5;
        }}
        .category-section {{
            margin-bottom: 30px;
        }}
        .category-title {{
            font-size: 1.1em;
            color: #635bff;
            margin-bottom: 10px;
        }}
        .amount-positive {{
            color: #28a745;
        }}
        .amount-negative {{
            color: #dc3545;
        }}
        .badge {{
            display: inline-block;
            padding: 4px 8px;
            border-radius: 12px;
            font-size: 0.8em;
            font-weight: 600;
            margin-left: 5px;
        }}
        .badge-domestic {{ background-color: #d4edda; color: #155724; }}
        .badge-eu-vat {{ background-color: #fff3cd; color: #856404; }}
        .badge-eu-no-vat {{ background-color: #cce5ff; color: #004085; }}
        .badge-extra-eu {{ background-color: #f8d7da; color: #721c24; }}
        .badge-unknown {{ background-color: #d1ecf1; color: #0c5460; }}
        .badge-refund {{ background-color: #f5c6cb; color: #721c24; }}
    </style>
</head>
<body>
    <h1>Lavender Report</h1>
    <p><strong>Period:</strong> {start_date} to {end_date}</p>
    
    <div class="summary">
        <h2>Summary</h2>
        <div class="summary-group">
            <div class="summary-group-label">Activity</div>
            <div class="summary-grid">
            <div class="summary-item">
                <strong>Number of Payments</strong>
                <span class="summary-value">{nb_payments}</span>
            </div>
            <div class="summary-item">
                <strong>Total Payments</strong>
                <span class="summary-value">{format_amount(total_payments, arg_currency)}</span>
            </div>
            <div class="summary-item">
                <strong>Number of Refunds</strong>
                <span class="summary-value">{nb_refunds}</span>
            </div>
            <div class="summary-item">
                <strong>Total Refunds</strong>
                <span class="summary-value">{format_amount(total_refunds, arg_currency)}</span>
            </div>
            <div class="summary-item">
                <strong>Net Total</strong>
                <span class="summary-value">{format_amount(total_payments - total_refunds, arg_currency)}</span>
            </div>
            </div>
        </div>
        <div class="summary-group">
            <div class="summary-group-label">Stripe fees</div>
            <div class="summary-grid">{fee_cards}
            </div>
        </div>
        <div class="summary-group">
            <div class="summary-group-label">VAT categories</div>
            <div class="summary-grid">{vat_cards}
            </div>
        </div>{french_vat_group}
    </div>
'''
    
    # Classification warnings box
    warned = [t for t in (
        transactions_in_country + transactions_in_eu_with_vat + transactions_in_eu_without_vat +
        transactions_outside_eu + transactions_unknown_country
    ) if t["warnings"]]
    if warned:
        html_content += f'''
    <div class="category-section" style="background-color: #fff3cd; padding: 15px; border-radius: 8px;">
        <div class="category-title" style="color: #856404;">
            Warnings ({len(warned)}) - possible classification mismatches, review before declaring
        </div>
        <table>
            <thead>
                <tr>
                    <th>Date</th>
                    <th>Amount</th>
                    <th>Country</th>
                    <th>VAT Number</th>
                    <th>Email</th>
                    <th>Warning</th>
                </tr>
            </thead>
            <tbody>
'''
        for t in warned:
            html_content += f'''
                <tr>
                    <td>{format_date(t['date'])}</td>
                    <td>{format_amount(t['amount'], t['currency'])}</td>
                    <td>{country_flag(t['country'])} {t['country']}</td>
                    <td>{html.escape(t['vat_number'])}</td>
                    <td>{html.escape(t['email'])}</td>
                    <td>{html.escape('; '.join(t['warnings']))}</td>
                </tr>
'''
        html_content += '''            </tbody>
        </table>
    </div>
'''
    
    # Add domestic transactions, split consumers / businesses
    html_content += generate_category_section(
        [t for t in transactions_in_country if not t['b2b']], f"Domestic B2C ({arg_country})"
    )
    html_content += generate_category_section(
        [t for t in transactions_in_country if t['b2b']], f"Domestic B2B ({arg_country})"
    )
    
    # Add EU with VAT transactions
    html_content += generate_category_section(
        transactions_in_eu_with_vat, "Intra-EU transactions (with VAT)"
    )
    
    # Add EU without VAT transactions
    html_content += generate_category_section(
        transactions_in_eu_without_vat, "Intra-EU transactions (reverse-charged VAT)"
    )
    
    # Add extra-EU transactions
    html_content += generate_category_section(
        transactions_outside_eu, "Extra-EU transactions"
    )
    
    # Add unknown country transactions
    html_content += generate_category_section(
        transactions_unknown_country, "Unknown transactions"
    )
    
    # Add refunds
    if transactions_refunds:
        html_content += f'''
    <div class="category-section">
        <div class="category-title">
            Refunded transactions - {len(transactions_refunds)} transactions
        </div>
        <table>
            <thead>
                <tr>
                    <th>#</th>
                    <th>Date</th>
                    <th>Amount</th>
                    <th>Currency</th>
                </tr>
            </thead>
            <tbody>
'''
        for i, t in enumerate(transactions_refunds, start=1):
            html_content += f'''
                <tr>
                    <td>{i}</td>
                    <td>{format_date(t['date'])}</td>
                    <td class="amount-negative">{format_amount(t['amount'], t['currency'])}</td>
                    <td>{t['currency']}</td>
                </tr>
'''
        html_content += '''            </tbody>
        </table>
    </div>
'''
    
    html_content += '''
</body>
</html>
'''
    
    return html_content


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
            nb_payments, total_payments, total_fees, addon_fees, nb_refunds, total_refunds,
            arg_country
        )
        with open(output_filename, 'w', newline='', encoding='utf-8') as csvfile:
            writer = csv.writer(csvfile)
            writer.writerows(csv_data)
        print(f"CSV report exported successfully to {output_filename}")
    
    elif export_format == 'html':
        html_content = generate_html_report(
            transactions_in_country, transactions_in_eu_with_vat, transactions_in_eu_without_vat,
            transactions_outside_eu, transactions_unknown_country, transactions_refunds,
            nb_payments, total_payments, total_fees, addon_fees, nb_refunds, total_refunds,
            arg_country, start_date, end_date
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
print(f"\nRefunded transactions: {nb_refunds} | Total: {format_amount(total_refunds, arg_currency)}")
for i, t in enumerate(transactions_refunds, start=1):
    print(f"  {i}. Amount: {format_amount(t['amount'], t['currency'])} - Date: {datetime.fromtimestamp(t['date'], pytz.utc).strftime('%Y-%m-%d %H:%M:%S')}")
