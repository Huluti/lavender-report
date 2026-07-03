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
args = parser.parse_args()

arg_country = args.country
arg_year = args.year
arg_month = args.month
export_format = args.export
output_filename = args.output

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
                    customer_email = customer.email or "No email"

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
                                tax_rate_details = tax.tax_rate_details
                                if tax_rate_details:
                                    # Retrieve the tax rate details
                                    tax_rate = stripe.TaxRate.retrieve(tax_rate_details.tax_rate)
                                    if tax_rate.country:
                                        country = tax_rate.country

                            # Extract VAT number if available
                            customer_tax_ids = invoice.customer_tax_ids or []
                            for tax_id in customer_tax_ids:
                                if tax_id.type == "eu_vat":
                                    vat_number = tax_id.value
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
        rounded_amount = int(Decimal(str(t['amount'])).quantize(0, ROUND_HALF_UP))
        print(
            f" {i}. Amount: {t['amount']:.2f} {t['currency']} "
            f"(Rounded: {rounded_amount} {t['currency']}) "
            f"- TVA: {t['vat_number']} - Country: {t['country']} "
            f"- Date: {datetime.fromtimestamp(t['date'], pytz.utc).strftime('%Y-%m-%d %H:%M:%S')} "
            f"- Email: {t['email']} - Status: {t['status']} "
            f"- Fees: {t['fee']:.2f} {t['currency']}"
        )


def format_date(timestamp):
    """Format timestamp to readable date string."""
    return datetime.fromtimestamp(timestamp, pytz.utc).strftime('%Y-%m-%d %H:%M:%S')


def generate_csv_report(
    transactions_in_country, transactions_in_eu_with_vat, transactions_in_eu_without_vat,
    transactions_outside_eu, transactions_unknown_country, transactions_refunds,
    nb_payments, total_payments, total_fees, nb_refunds, total_refunds,
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
    output.append(["Total Refunds", f"{nb_refunds}"])
    output.append(["Total Refund Amount", f"{total_refunds:.2f} EUR"])
    output.append(["Net Total", f"{total_payments - total_refunds:.2f} EUR"])
    
    return output


def generate_html_report(
    transactions_in_country, transactions_in_eu_with_vat, transactions_in_eu_without_vat,
    transactions_outside_eu, transactions_unknown_country, transactions_refunds,
    nb_payments, total_payments, total_fees, nb_refunds, total_refunds,
    arg_country, start_date, end_date
):
    """Generate HTML report of all transactions."""
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
        <div class="summary-grid">
            <div class="summary-item">
                <strong>Number of Payments</strong>
                <span class="summary-value">{nb_payments}</span>
            </div>
            <div class="summary-item">
                <strong>Total Payments</strong>
                <span class="summary-value">{total_payments:.2f} EUR</span>
            </div>
            <div class="summary-item">
                <strong>Total Stripe Fees</strong>
                <span class="summary-value">{total_fees:.2f} EUR</span>
            </div>
            <div class="summary-item">
                <strong>Number of Refunds</strong>
                <span class="summary-value">{nb_refunds}</span>
            </div>
            <div class="summary-item">
                <strong>Total Refunds</strong>
                <span class="summary-value">{total_refunds:.2f} EUR</span>
            </div>
            <div class="summary-item">
                <strong>Net Total</strong>
                <span class="summary-value">{total_payments - total_refunds:.2f} EUR</span>
            </div>
        </div>
    </div>
'''
    
    # Add domestic transactions
    domestic_total = sum(t['amount'] for t in transactions_in_country)
    if transactions_in_country:
        html_content += f'''
    <div class="category-section">
        <div class="category-title">
            Domestic transactions ({arg_country}) - {len(transactions_in_country)} transactions | Total: {domestic_total:.2f} EUR
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
                    <th>Email</th>
                    <th>Status</th>
                    <th>Fees</th>
                </tr>
            </thead>
            <tbody>
'''
        for i, t in enumerate(transactions_in_country, start=1):
            rounded_amount = int(Decimal(str(t['amount'])).quantize(0, ROUND_HALF_UP))
            vat_badge = "Yes" if t['vat_applied'] else "No"
            html_content += f'''
                <tr>
                    <td>{i}</td>
                    <td>{format_date(t['date'])}</td>
                    <td class="amount-positive">{t['amount']:.2f} {t['currency']}</td>
                    <td>{rounded_amount} {t['currency']}</td>
                    <td>{t['country']}</td>
                    <td>{html.escape(t['vat_number'])}</td>
                    <td>{vat_badge}</td>
                    <td>{html.escape(t['email'])}</td>
                    <td>{t['status']}</td>
                    <td>{t['fee']:.2f} {t['currency']}</td>
                </tr>
'''
        html_content += '''            </tbody>
        </table>
    </div>
'''
    
    # Add EU with VAT transactions
    eu_vat_total = sum(t['amount'] for t in transactions_in_eu_with_vat)
    if transactions_in_eu_with_vat:
        html_content += f'''
    <div class="category-section">
        <div class="category-title">
            Intra-EU transactions (with VAT) - {len(transactions_in_eu_with_vat)} transactions | Total: {eu_vat_total:.2f} EUR
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
                    <th>Email</th>
                    <th>Status</th>
                    <th>Fees</th>
                </tr>
            </thead>
            <tbody>
'''
        for i, t in enumerate(transactions_in_eu_with_vat, start=1):
            rounded_amount = int(Decimal(str(t['amount'])).quantize(0, ROUND_HALF_UP))
            html_content += f'''
                <tr>
                    <td>{i}</td>
                    <td>{format_date(t['date'])}</td>
                    <td class="amount-positive">{t['amount']:.2f} {t['currency']}</td>
                    <td>{rounded_amount} {t['currency']}</td>
                    <td>{t['country']}</td>
                    <td>{html.escape(t['vat_number'])}</td>
                    <td>{html.escape(t['email'])}</td>
                    <td>{t['status']}</td>
                    <td>{t['fee']:.2f} {t['currency']}</td>
                </tr>
'''
        html_content += '''            </tbody>
        </table>
    </div>
'''
    
    # Add EU without VAT transactions
    eu_no_vat_total = sum(t['amount'] for t in transactions_in_eu_without_vat)
    if transactions_in_eu_without_vat:
        html_content += f'''
    <div class="category-section">
        <div class="category-title">
            Intra-EU transactions (reverse-charged VAT) - {len(transactions_in_eu_without_vat)} transactions | Total: {eu_no_vat_total:.2f} EUR
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
                    <th>Email</th>
                    <th>Status</th>
                    <th>Fees</th>
                </tr>
            </thead>
            <tbody>
'''
        for i, t in enumerate(transactions_in_eu_without_vat, start=1):
            rounded_amount = int(Decimal(str(t['amount'])).quantize(0, ROUND_HALF_UP))
            html_content += f'''
                <tr>
                    <td>{i}</td>
                    <td>{format_date(t['date'])}</td>
                    <td class="amount-positive">{t['amount']:.2f} {t['currency']}</td>
                    <td>{rounded_amount} {t['currency']}</td>
                    <td>{t['country']}</td>
                    <td>{html.escape(t['vat_number'])}</td>
                    <td>{html.escape(t['email'])}</td>
                    <td>{t['status']}</td>
                    <td>{t['fee']:.2f} {t['currency']}</td>
                </tr>
'''
        html_content += '''            </tbody>
        </table>
    </div>
'''
    
    # Add extra-EU transactions
    extra_eu_total = sum(t['amount'] for t in transactions_outside_eu)
    if transactions_outside_eu:
        html_content += f'''
    <div class="category-section">
        <div class="category-title">
            Extra-EU transactions - {len(transactions_outside_eu)} transactions | Total: {extra_eu_total:.2f} EUR
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
                    <th>Email</th>
                    <th>Status</th>
                    <th>Fees</th>
                </tr>
            </thead>
            <tbody>
'''
        for i, t in enumerate(transactions_outside_eu, start=1):
            rounded_amount = int(Decimal(str(t['amount'])).quantize(0, ROUND_HALF_UP))
            html_content += f'''
                <tr>
                    <td>{i}</td>
                    <td>{format_date(t['date'])}</td>
                    <td class="amount-positive">{t['amount']:.2f} {t['currency']}</td>
                    <td>{rounded_amount} {t['currency']}</td>
                    <td>{t['country']}</td>
                    <td>{html.escape(t['vat_number'])}</td>
                    <td>{html.escape(t['email'])}</td>
                    <td>{t['status']}</td>
                    <td>{t['fee']:.2f} {t['currency']}</td>
                </tr>
'''
        html_content += '''            </tbody>
        </table>
    </div>
'''
    
    # Add unknown country transactions
    unknown_total = sum(t['amount'] for t in transactions_unknown_country)
    if transactions_unknown_country:
        html_content += f'''
    <div class="category-section">
        <div class="category-title">
            Unknown transactions - {len(transactions_unknown_country)} transactions | Total: {unknown_total:.2f} EUR
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
                    <th>Email</th>
                    <th>Status</th>
                    <th>Fees</th>
                </tr>
            </thead>
            <tbody>
'''
        for i, t in enumerate(transactions_unknown_country, start=1):
            rounded_amount = int(Decimal(str(t['amount'])).quantize(0, ROUND_HALF_UP))
            html_content += f'''
                <tr>
                    <td>{i}</td>
                    <td>{format_date(t['date'])}</td>
                    <td class="amount-positive">{t['amount']:.2f} {t['currency']}</td>
                    <td>{rounded_amount} {t['currency']}</td>
                    <td>{t['country']}</td>
                    <td>{html.escape(t['vat_number'])}</td>
                    <td>{html.escape(t['email'])}</td>
                    <td>{t['status']}</td>
                    <td>{t['fee']:.2f} {t['currency']}</td>
                </tr>
'''
        html_content += '''            </tbody>
        </table>
    </div>
'''
    
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
                    <td class="amount-negative">{t['amount']:.2f} {t['currency']}</td>
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
            nb_payments, total_payments, total_fees, nb_refunds, total_refunds,
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
            nb_payments, total_payments, total_fees, nb_refunds, total_refunds,
            arg_country, start_date, end_date
        )
        with open(output_filename, 'w', encoding='utf-8') as htmlfile:
            htmlfile.write(html_content)
        print(f"HTML report exported successfully to {output_filename}")


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
