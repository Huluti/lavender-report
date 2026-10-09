"""HTML report generation for Lavender Report."""

import html
from decimal import ROUND_HALF_UP, Decimal


def country_flag(country):
    """Flag emoji for an ISO country code, empty string otherwise."""
    if len(country) == 2 and country.isalpha() and country.isupper():
        return chr(0x1F1E6 + ord(country[0]) - ord('A')) + chr(0x1F1E6 + ord(country[1]) - ord('A'))
    return ""


def generate_category_section(transactions, title, format_amount, format_date, arg_currency):
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


def generate_html_report(
    transactions_in_country, transactions_in_eu_with_vat, transactions_in_eu_without_vat,
    transactions_outside_eu, transactions_unknown_country, transactions_refunds,
    nb_payments, total_payments, total_fees, addon_fees, nb_refunds, total_refunds,
    arg_country, start_date, end_date, arg_currency, format_amount, format_date, ht_amount
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
        [t for t in transactions_in_country if not t['b2b']], f"Domestic B2C ({arg_country})",
        format_amount, format_date, arg_currency
    )
    html_content += generate_category_section(
        [t for t in transactions_in_country if t['b2b']], f"Domestic B2B ({arg_country})",
        format_amount, format_date, arg_currency
    )
    
    # Add EU with VAT transactions
    html_content += generate_category_section(
        transactions_in_eu_with_vat, "Intra-EU transactions (with VAT)",
        format_amount, format_date, arg_currency
    )
    
    # Add EU without VAT transactions
    html_content += generate_category_section(
        transactions_in_eu_without_vat, "Intra-EU transactions (reverse-charged VAT)",
        format_amount, format_date, arg_currency
    )
    
    # Add extra-EU transactions
    html_content += generate_category_section(
        transactions_outside_eu, "Extra-EU transactions",
        format_amount, format_date, arg_currency
    )
    
    # Add unknown country transactions
    html_content += generate_category_section(
        transactions_unknown_country, "Unknown transactions",
        format_amount, format_date, arg_currency
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
