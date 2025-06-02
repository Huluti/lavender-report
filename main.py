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

# Parse arguments
now = datetime.now()
current_year = now.year
last_month = now.month - 1 or 12  # If month is January (1), last month should be December (12)

parser = argparse.ArgumentParser(description="Lavender Report")
parser.add_argument('--country', type=str, help="Country", default="FR")
parser.add_argument('--year', type=int, help="Year", default=current_year)
parser.add_argument('--month', type=int, help="Month", default=last_month)
args = parser.parse_args()

arg_country = args.country
arg_year = args.year
arg_month = args.month

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

# Transaction categories
transactions_in_country = []
transactions_in_eu_with_vat = []
transactions_in_eu_without_vat = []
transactions_outside_eu = []
transactions_unknown_country = []
transactions_refunds = []

# Initialize progress counter
progress_count = 0
total_transactions = len(list(balance_transactions.auto_paging_iter()))

print(f"Processing {total_transactions} balance transactions...")

# Process balance transactions
for balance_transaction in balance_transactions.auto_paging_iter():
    progress_count += 1
    sys.stdout.write(f"\rProcessing transaction {progress_count}/{total_transactions}...")
    sys.stdout.flush()
    
    # Skip non-payment transactions (transfers, adjustments, etc.)
    if balance_transaction.type not in ['charge', 'payment', 'refund']:
        continue
    
    # Convert amounts from cents to full currency units
    amount = balance_transaction.amount / 100
    fee = balance_transaction.fee / 100
    currency = balance_transaction.currency.upper()
    
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
    total_fees += fee
    
    # Initialize default values
    country = 'Unknown'
    vat_number = 'Not available'
    vat_applied = False
    customer_email = "No email"
    status = "succeeded"
    
    # Get source details (charge, payment_intent, etc.)
    source = balance_transaction.source
    if source and hasattr(source, 'object'):
        try:
            if source.object == 'charge':
                # Get customer email from charge
                if source.customer:
                    customer = stripe.Customer.retrieve(source.customer)
                    customer_email = customer.get("email", "No email")
                
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
                            tax_amounts = invoice.get("total_taxes", [])
                            for tax in tax_amounts:
                                if not vat_applied and tax.amount > 0:
                                    vat_applied = True
                                tax_rate_details = tax.get("tax_rate_details")
                                if tax_rate_details:
                                    # Retrieve the tax rate details
                                    tax_rate = stripe.TaxRate.retrieve(tax_rate_details.tax_rate)
                                    if tax_rate.country:
                                        country = tax_rate.country
                            
                            # Extract VAT number if available
                            customer_tax_ids = invoice.get("customer_tax_ids", [])
                            for tax_id in customer_tax_ids:
                                if tax_id.get("type") == "eu_vat":
                                    vat_number = tax_id.get("value")
                                    break
                    
                    except stripe.error.StripeError as e:
                        # No invoice payment found or error occurred
                        pass
            
        except stripe.error.StripeError as e:
            print(f"\nError retrieving details for transaction {balance_transaction.id}: {e}")
            continue
    
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
        "fee": fee,
    }
    
    # Categorize transaction
    if country == arg_country:
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

# Clear progress indicator
sys.stdout.write('\r' + ' ' * 50 + '\r')
sys.stdout.flush()
print("Processing completed!")

# Summary
print("\nSummary:")
print(f"Number of payments: {nb_payments}")
print(f"Total: {total_payments:.2f} EUR")
print(f"Total Stripe fees: {total_fees:.2f} EUR")

# Function to print details for each transaction
def print_transaction_details(transactions, category_name):
    print(f"\n{category_name}: {len(transactions)} | Total: {sum(t['amount'] for t in transactions):.2f} EUR")
    for i, t in enumerate(transactions, start=1):
        print(
            f" {i}. Amount: {t['amount']:.2f} {t['currency']} - Date: {datetime.fromtimestamp(t['date'], pytz.utc).strftime('%Y-%m-%d %H:%M:%S')} - "
            f"Email: {t['email']} - Status: {t['status']} - "
            f"Country: {t['country']} - TVA: {t['vat_number']} - Fees: {t['fee']:.2f} {t['currency']}"
        )

# Payments
print_transaction_details(transactions_in_country, "Domestic transactions (your company's country)")
print_transaction_details(transactions_in_eu_with_vat, "Intra-EU transactions (with VAT)")
print_transaction_details(transactions_in_eu_without_vat, "Intra-EU transactions (with reverse-charged VAT)")
print_transaction_details(transactions_outside_eu, "Extra-EU transactions")
print_transaction_details(transactions_unknown_country, "Unknown transactions")

# Refunds
print(f"\nRefunded transactions: {nb_refunds} | Total: {total_refunds:.2f} EUR")
for i, t in enumerate(transactions_refunds, start=1):
    print(f"  {i}. Amount: {t['amount']:.2f} {t['currency']} - Date: {datetime.fromtimestamp(t['date'], pytz.utc).strftime('%Y-%m-%d %H:%M:%S')}")