from datetime import datetime
from dotenv import load_dotenv
import stripe
import pytz
import os
import sys
import calendar
import argparse

load_dotenv()

# Stripe secret key
stripe.api_key = os.getenv("STRIPE_SECRET_KEY")

# Parse arguments

now = datetime.now()
current_year = now.year
last_month = now.month - 1 or 12  # If month is January (1), last month should be December (12)

parser = argparse.ArgumentParser(description="Example program")
parser.add_argument('--country', type=str, help="Country", default="FR")
parser.add_argument('--year', type=int, help="Year", default=current_year)
parser.add_argument('--month', type=int, help="Month", default=last_month)

args = parser.parse_args()

country = args.country
year = args.year
month = args.month

# Convert dates in timestamps (UTC+1)
def to_timestamp(date_str):
    tz = pytz.timezone("Europe/Paris")  # UTC+1
    dt = datetime.strptime(date_str, "%Y-%m-%d")  # Format to AAAA-MM-JJ
    dt_utc = tz.localize(dt).astimezone(pytz.utc)  # Convert to UTC
    return int(dt_utc.timestamp())

# Find first and last day of month
start_date = f"{year}-{month:02d}-01"
last_day = calendar.monthrange(year, month)[1]  # Nb days in the month
end_date = f"{year}-{month:02d}-{last_day}"

# Get timestamps
start_timestamp = to_timestamp(start_date)
end_timestamp = to_timestamp(end_date) + 86399  # Very last moment of last day

print(f"Fetch transactions from {start_date} to {end_date}")

# Fetch payments from given period
# Fetch more than you need, filter later
charges = stripe.Charge.list(
    # go 1 week back
    created={"gte": start_timestamp - 7 * 24 * 60 * 60},
    limit=100,
    expand=['data.customer', 'data.balance_transaction', 'data.payment_intent']
)

# Filter charges where balance_transaction happened this month
filtered_charges = [
    charge for charge in charges.auto_paging_iter()
    if charge.balance_transaction and start_timestamp <= charge.balance_transaction.created < end_timestamp
]

# Fetch refunds for given period
refunds = stripe.Refund.list(
    created={"gte": start_timestamp, "lte": end_timestamp},
    limit=100
)

total_eur = 0
transaction_count = 0
total_fees = 0
total_refunds = 0

# Transaction categories
transactions_in_country = []
transactions_in_eu_with_vat = []
transactions_in_eu_without_vat = []
transactions_outside_eu = []
transactions_unknown_country = []
transactions_refunds = []

# Initialize progress counter
progress_count = 0
total_transactions = len(filtered_charges)

# Loop over payments
for charge in filtered_charges:
    # Update progress
    progress_count += 1
    sys.stdout.write(f"\rFetch transaction {progress_count}/{total_transactions}...")
    sys.stdout.flush()

    if not charge.paid:
        continue

    payment = charge.payment_intent  # Fetch full PaymentIntent details

    customer_email = charge.customer.get("email", "No email")

    # Fetch balance transactions associated to the charge
    balance_transaction = charge.balance_transaction

    country = 'Unknown'
    vat_number = 'Not available'
    vat_applied = False
    fee = 0
    invoice_id = payment.get("invoice")
    if invoice_id:
        # Retrieve the Invoice
        invoice = stripe.Invoice.retrieve(invoice_id)

        # Extract country from the tax rate used
        tax_amounts = invoice.get("total_tax_amounts", [])
        for tax in tax_amounts:
            if not vat_applied and tax.amount > 0:
                vat_applied = True
            tax_rate_id = tax.get("tax_rate")
            if tax_rate_id:
                # Retrieve the tax rate details
                tax_rate = stripe.TaxRate.retrieve(tax_rate_id)
                if tax_rate.country:
                    country = tax_rate.country

        # Extract VAT number if available
        customer_tax_ids = invoice.get("customer_tax_ids", [])
        for tax_id in customer_tax_ids:
            if tax_id.get("type") == "eu_vat":
                vat_number = tax_id.get("value")
                break

    if balance_transaction:
        # Check if exchange rate exists
        exchange_rate = balance_transaction.get('exchange_rate', None)
        # Convert to full currency units
        amount_received = balance_transaction['amount'] / 100
        # Stripe fee (convert to full currency units)
        fee = balance_transaction.get('fee', 0) / 100
        total_fees += fee  # Accumulate the fees
        currency = "EUR" if exchange_rate else payment["currency"].upper()
    else:
        # Convert to full currency units
        amount_received = payment["amount_received"] / 100
        currency = payment["currency"].upper()

    total_eur += amount_received
    transaction_count += 1

    # Transaction details dictionary
    transaction_details = {
        "date": payment['created'],
        "status": payment['status'],
        "amount": amount_received,
        "currency": currency,
        "email": customer_email,
        "country": country,
        "vat_number": vat_number,
        "vat_applied": vat_applied,
        "fee": fee,
    }

    # Categorize transaction
    if country == country:
        transactions_in_country.append(transaction_details)
    elif country in [
        "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "DE", "GR", "HU",
        "IE", "IT", "LV", "LT", "LU", "MT", "NL", "PL", "PT", "RO", "SK", "SI",
        "ES", "SE"
    ]:
        if vat_applied:
            transactions_in_eu_with_vat.append(transaction_details)
        else:
            transactions_in_eu_without_vat.append(transaction_details)
    elif country == "Unknown":
        transactions_unknown_country.append(transaction_details)
    else:
        transactions_outside_eu.append(transaction_details)

sys.stdout.write('\r' + ' ' * 50 + '\r')
sys.stdout.flush()

# Initialize progress counter
progress_count = 0
total_transactions = len(refunds)

for refund in refunds:
    # Update progress
    progress_count += 1
    sys.stdout.write(f"\rFetch refund {progress_count}/{total_transactions}...")
    sys.stdout.flush()
 
    balance_transaction = stripe.BalanceTransaction.retrieve(
        refund['balance_transaction'])

    amount_refunded = balance_transaction['amount'] / 100
    currency = balance_transaction['currency'].upper()

    total_refunds += amount_refunded

    # Categorize refunds
    refund_details = {
        "amount": amount_refunded,
        "currency": currency,
    }

    transactions_refunds.append(refund_details)

# Finish progress counter
sys.stdout.write('\r' + ' ' * 50 + '\r')
sys.stdout.flush()
print("Fetching done!")

# Summary
print("\nSummary:")
print(f"Number of transactions: {transaction_count}")
print(f"Total in EUR: {total_eur:.2f} EUR")
print(f"Total Stripe fees: {total_fees:.2f} EUR")

# Function to print details for each transaction

def print_transaction_details(transactions, category_name):
    print(
        f"\n{category_name}: {len(transactions)} | Total: {sum(t['amount'] for t in transactions):.2f} EUR")
    for i, t in enumerate(transactions, start=1):
        print(
            f" {i}. Amount: {t['amount']:.2f} {t['currency']} - Date: {datetime.fromtimestamp(t['date'], pytz.utc).strftime('%Y-%m-%d %H:%M:%S')} - "
            f"Email: {t['email']} - Status: {t['status']} - "
            f"Country: {t['country']} - TVA: {t['vat_number']} - Fees: {t['fee']:.2f} {t['currency']}"
        )

# Payments

print_transaction_details(transactions_in_country, "Domestic transactions (your company’s country)")
print_transaction_details(transactions_in_eu_with_vat, "Intra-EU transactions (with VAT)")
print_transaction_details(transactions_in_eu_without_vat, "Intra-EU transactions (with reverse-charged VAT")
print_transaction_details(transactions_outside_eu, "Extra-EU transactions")
print_transaction_details(transactions_unknown_country, "Unknown transactions")

# Refunds

print(
    f"\nRefunded transactions: {len(transactions_refunds)} | Total: {total_refunds:.2f} EUR")
for i, t in enumerate(transactions_refunds, start=1):
    print(f"  {i}. Amount: {t['amount']:.2f} {t['currency']}")
