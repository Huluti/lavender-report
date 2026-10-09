"""HTML report generation for Lavender Report."""

import html
from decimal import ROUND_HALF_UP, Decimal


def country_flag(country):
    """Flag emoji for an ISO country code, empty string otherwise."""
    if len(country) == 2 and country.isalpha() and country.isupper():
        return chr(0x1F1E6 + ord(country[0]) - ord('A')) + chr(0x1F1E6 + ord(country[1]) - ord('A'))
    return ""


def _card(label, value):
    return f'''
            <div class="summary-item">
                <strong>{label}</strong>
                <span class="summary-value">{value}</span>
            </div>'''


def _group(label, cards):
    return f'''
        <div class="summary-group">
            <div class="summary-group-label">{label}</div>
            <div class="summary-grid">{cards}
            </div>
        </div>'''


def _converted_amount(convert, amount, currency, arg_currency, format_amount):
    """Formatted converted amount, or a dash when no rate is available."""
    converted = convert(amount, currency)
    if converted is None:
        return '&mdash;'
    return format_amount(converted, arg_currency)


def _fmt_or_dash(value, arg_currency, format_amount):
    """Format a converted value, dash when no rate was available."""
    if value is None:
        return '&mdash;'
    return format_amount(value, arg_currency)


def _sum_converted_value(convert, transactions, field):
    """Converted total over transactions (each conversion rounded to the
    cent, so displayed rows and totals reconcile), or None when some
    currency has no rate."""
    total = 0
    for t in transactions:
        converted = convert(t[field], t['currency'])
        if converted is None:
            return None
        total += converted
    return total


def _sum_converted(convert, transactions, field, arg_currency, format_amount):
    """Formatted converted total over transactions, dash when a rate is
    missing."""
    total = _sum_converted_value(convert, transactions, field)
    if total is None:
        return '&mdash;'
    return format_amount(total, arg_currency)


def generate_category_section(transactions, title, format_amount, format_date, arg_currency, convert):
    """Generate the HTML section for one category of transactions.

    All transactions share the tab's currency; the converted column is
    added when that currency is not the default report currency.
    """
    if not transactions:
        return ""
    section_currency = transactions[0]['currency']
    show_converted = section_currency != arg_currency
    total = sum(t['amount'] for t in transactions)
    converted_cell = ""
    converted_header = ""
    if show_converted:
        converted_header = f'''
                    <th>Amount ({arg_currency})</th>'''
    rows = ""
    for i, t in enumerate(transactions, start=1):
        vat_badge = "Yes" if t['vat_applied'] else "No"
        if t['warnings']:
            warning_prefix = f'<span title="{html.escape("; ".join(t["warnings"]))}">&#9888;&#65039; </span>'
        else:
            warning_prefix = ""
        # Rounded amount for the declaration: the whole-unit rounding
        # of the amount in the default currency (converted first for
        # non-default currencies)
        if show_converted:
            converted_cell = f'''
                    <td>{_converted_amount(convert, t['amount'], t['currency'], arg_currency, format_amount)}</td>'''
            converted = convert(t['amount'], t['currency'])
            if converted is None:
                rounded_cell = '&mdash;'
            else:
                rounded_cell = format_amount(
                    int(Decimal(str(converted)).quantize(0, ROUND_HALF_UP)), arg_currency
                )
        else:
            converted_cell = ""
            rounded_cell = format_amount(
                int(Decimal(str(t['amount'])).quantize(0, ROUND_HALF_UP)), t['currency']
            )
        rows += f'''
                <tr>
                    <td>{warning_prefix}{i}</td>
                    <td>{format_date(t['date'])}</td>
                    <td class="amount-positive">{format_amount(t['amount'], t['currency'])}</td>{converted_cell}
                    <td>{rounded_cell}</td>
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
            {title} - {len(transactions)} transactions | Total: {format_amount(total, section_currency)}{_converted_total_suffix(convert, transactions, section_currency, arg_currency, format_amount)}
        </div>
        <table>
            <thead>
                <tr>
                    <th>#</th>
                    <th>Date</th>
                    <th>Amount</th>{converted_header}
                    <th>Rounded{f" ({arg_currency})" if show_converted else ""}</th>
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


def _converted_total_suffix(convert, transactions, section_currency, arg_currency, format_amount):
    """Append the converted total to a section title, when relevant."""
    if section_currency == arg_currency:
        return ""
    converted = _sum_converted(convert, transactions, 'amount', arg_currency, format_amount)
    return f" ({converted} converted)"


def _currency_activity_cards(currency, stats, arg_currency, format_amount, show_converted, convert,
                             payments_txns=None, refund_txns=None):
    """Activity + fee cards for one currency, native amounts. Converted
    cards sum per-transaction conversions, matching the table rows."""
    cards = _card(f"Payments ({currency})", f"{stats['payments']} | {format_amount(stats['payments_total'], currency)}")
    cards += _card(f"Refunds ({currency})", f"{stats['refunds']} | {format_amount(stats['refunds_total'], currency)}")
    cards += _card(f"Net Total ({currency})", format_amount(stats['payments_total'] - stats['refunds_total'], currency))
    cards += _card(f"Stripe Fees ({currency})", format_amount(stats['fees'], currency))
    if stats['addon_fees']:
        cards += _card(f"Add-on Fees ({currency})", format_amount(stats['addon_fees'], currency))
    if show_converted:
        payments_converted = _sum_converted_value(convert, payments_txns or [], 'amount')
        refunds_converted = _sum_converted_value(convert, refund_txns or [], 'amount')
        if payments_converted is not None and refunds_converted is not None:
            cards += _card(
                f"Payments ({arg_currency})",
                format_amount(payments_converted, arg_currency)
            )
            cards += _card(
                f"Net Total ({arg_currency})",
                format_amount(payments_converted - refunds_converted, arg_currency)
            )
        fees_converted = convert(stats['fees'], currency)
        if fees_converted is not None:
            cards += _card(f"Stripe Fees ({arg_currency})", format_amount(fees_converted, arg_currency))
    return cards


def _warnings_box(warned, format_date, format_amount):
    """Warnings box for a set of transactions, empty when none."""
    if not warned:
        return ""
    box = f'''
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
        box += f'''
                <tr>
                    <td>{format_date(t['date'])}</td>
                    <td>{format_amount(t['amount'], t['currency'])}</td>
                    <td>{country_flag(t['country'])} {t['country']}</td>
                    <td>{html.escape(t['vat_number'])}</td>
                    <td>{html.escape(t['email'])}</td>
                    <td>{html.escape('; '.join(t['warnings']))}</td>
                </tr>
'''
    box += '''            </tbody>
        </table>
    </div>
'''
    return box


def generate_html_report(
    transactions_in_country, transactions_in_eu_with_vat, transactions_in_eu_without_vat,
    transactions_outside_eu, transactions_unknown_country, transactions_refunds,
    arg_country, start_date, end_date, arg_currency, format_amount, format_date, ht_amount,
    convert, currency_stats, rates, manual_rates, generated_at
):
    """Generate HTML report of all transactions, one tab per currency
    plus an overview tab with the whole situation."""
    currencies = sorted(currency_stats.keys())

    all_categorized = (
        transactions_in_country + transactions_in_eu_with_vat + transactions_in_eu_without_vat +
        transactions_outside_eu + transactions_unknown_country
    )

    # Per-currency filtered category lists
    def by_currency(transactions, currency):
        return [t for t in transactions if t['currency'] == currency]

    # --- Overview tab: whole situation ---
    overview_groups = ""

    # Rates used, so the conversion is auditable
    rate_cards = ""
    for currency in currencies:
        if currency == "EUR":
            continue
        rate = rates.get(currency)
        if rate is not None:
            source = "manual" if currency in manual_rates else "douane.gouv.fr"
            rate_cards += _card(f"Rate {currency} ({source})", f"1 EUR = {rate} {currency}")
    if arg_currency != "EUR":
        rate = rates.get(arg_currency)
        if rate is not None:
            source = "manual" if arg_currency in manual_rates else "douane.gouv.fr"
            rate_cards += _card(f"Rate {arg_currency} ({source})", f"1 EUR = {rate} {arg_currency}")
    if rate_cards:
        overview_groups += _group("Exchange rates (whole month)", rate_cards)

    # Activity per currency, native amounts only
    for currency in currencies:
        stats = currency_stats[currency]
        overview_groups += _group(
            f"Activity - {currency}",
            _currency_activity_cards(currency, stats, arg_currency, format_amount, False, convert)
        )

    # Whole situation: combined activity converted to the default currency.
    # Payments and refunds are converted per transaction (same rounding as
    # the table rows, so the visible figures reconcile); fees cover fee-only
    # balance transactions too and are converted per currency.
    payments_converted = _sum_converted_value(convert, all_categorized, 'amount')
    refunds_converted = _sum_converted_value(convert, transactions_refunds, 'amount')
    net_converted = (
        None if payments_converted is None or refunds_converted is None
        else payments_converted - refunds_converted
    )
    all_fees = [
        {"amount": currency_stats[c]['fees'], "currency": c}
        for c in currencies if currency_stats[c]['fees']
    ]
    overview_groups += _group(
        f"Whole situation (converted to {arg_currency})",
        _card("Total Payments", _fmt_or_dash(payments_converted, arg_currency, format_amount)) +
        _card("Total Refunds", _fmt_or_dash(refunds_converted, arg_currency, format_amount)) +
        _card("Net Total", _fmt_or_dash(net_converted, arg_currency, format_amount)) +
        _card("Total Stripe Fees", _sum_converted(convert, all_fees, 'amount', arg_currency, format_amount))
    )

    # VAT categories, converted combined
    vat_cards = _card(f"Domestic ({arg_country})", _sum_converted(convert, transactions_in_country, 'amount', arg_currency, format_amount))
    if any(not t['b2b'] for t in transactions_in_country):
        vat_cards += _card("Domestic B2C (HT)", _sum_converted(convert, [dict(t, amount=ht_amount(t)) for t in transactions_in_country if not t['b2b']], 'amount', arg_currency, format_amount))
    if any(t['b2b'] for t in transactions_in_country):
        vat_cards += _card("Domestic B2B (HT)", _sum_converted(convert, [dict(t, amount=ht_amount(t)) for t in transactions_in_country if t['b2b']], 'amount', arg_currency, format_amount))
    if transactions_in_eu_with_vat:
        vat_cards += _card("Intra-EU (with VAT)", _sum_converted(convert, transactions_in_eu_with_vat, 'amount', arg_currency, format_amount))
    vat_cards += _card("Intra-EU (reverse-charged)", _sum_converted(convert, transactions_in_eu_without_vat, 'amount', arg_currency, format_amount))
    vat_cards += _card("Extra-EU", _sum_converted(convert, transactions_outside_eu, 'amount', arg_currency, format_amount))
    if transactions_unknown_country:
        vat_cards += _card("Unknown", _sum_converted(convert, transactions_unknown_country, 'amount', arg_currency, format_amount))
    overview_groups += _group(f"VAT categories (converted to {arg_currency})", vat_cards)

    # French VAT declaration: all currencies converted to the default one
    french_b2c_txns = [
        t for t in transactions_in_country + transactions_in_eu_with_vat
        if t["tax_country"] == arg_country and t["vat_applied"] and not t["b2b"]
    ]
    french_cards = ""
    if french_b2c_txns:
        french_cards += _card("EU consumers, French VAT (HT)", _sum_converted(convert, [dict(t, amount=ht_amount(t)) for t in french_b2c_txns], 'amount', arg_currency, format_amount))
        french_cards += _card("TVA collected (B2C)", _sum_converted(convert, [dict(t, amount=t["tax_amount"]) for t in french_b2c_txns], 'amount', arg_currency, format_amount))
    domestic_b2c = [t for t in transactions_in_country if not t["b2b"]]
    domestic_b2b = [t for t in transactions_in_country if t["b2b"]]
    if domestic_b2c:
        french_cards += _card("Domestic B2C (HT)", _sum_converted(convert, [dict(t, amount=ht_amount(t)) for t in domestic_b2c], 'amount', arg_currency, format_amount))
    if domestic_b2b:
        french_cards += _card("Domestic B2B (HT)", _sum_converted(convert, [dict(t, amount=ht_amount(t)) for t in domestic_b2b], 'amount', arg_currency, format_amount))
    if french_cards:
        overview_groups += _group(f"French VAT declaration (converted to {arg_currency})", french_cards)

    # --- Per-currency tabs ---
    warned_all = [t for t in all_categorized if t["warnings"]]
    tab_buttons = '''
        <button class="tab-link active" onclick="openTab(event, 'tab-overview')">Overview</button>'''
    tab_contents = f'''
    <div id="tab-overview" class="tab-content active">
    <div class="summary">
        <h2>Summary - whole situation</h2>{overview_groups}
    </div>
    {_warnings_box(warned_all, format_date, format_amount)}
    </div>'''

    for currency in currencies:
        tab_id = f"tab-{currency.lower()}"
        stats = currency_stats[currency]
        show_converted = currency != arg_currency

        # VAT categories in native currency
        in_country = by_currency(transactions_in_country, currency)
        eu_vat = by_currency(transactions_in_eu_with_vat, currency)
        eu_no_vat = by_currency(transactions_in_eu_without_vat, currency)
        outside_eu = by_currency(transactions_outside_eu, currency)
        unknown = by_currency(transactions_unknown_country, currency)
        cur_refunds = by_currency(transactions_refunds, currency)
        cur_payments = in_country + eu_vat + eu_no_vat + outside_eu + unknown

        currency_groups = ""
        currency_groups += _group(
            "Activity",
            _currency_activity_cards(currency, stats, arg_currency, format_amount, show_converted, convert,
                                     cur_payments, cur_refunds)
        )

        cur_vat_cards = _card(f"Domestic ({arg_country})", format_amount(sum(t['amount'] for t in in_country), currency))
        if any(not t['b2b'] for t in in_country):
            cur_vat_cards += _card("Domestic B2C (HT)", format_amount(sum(ht_amount(t) for t in in_country if not t['b2b']), currency))
        if any(t['b2b'] for t in in_country):
            cur_vat_cards += _card("Domestic B2B (HT)", format_amount(sum(ht_amount(t) for t in in_country if t['b2b']), currency))
        if eu_vat:
            cur_vat_cards += _card("Intra-EU (with VAT)", format_amount(sum(t['amount'] for t in eu_vat), currency))
        cur_vat_cards += _card("Intra-EU (reverse-charged)", format_amount(sum(t['amount'] for t in eu_no_vat), currency))
        cur_vat_cards += _card("Extra-EU", format_amount(sum(t['amount'] for t in outside_eu), currency))
        if unknown:
            cur_vat_cards += _card("Unknown", format_amount(sum(t['amount'] for t in unknown), currency))
        currency_groups += _group("VAT categories", cur_vat_cards)

        # French VAT declaration for this currency (native amounts; these
        # feed the declaration after conversion at the official rate)
        cur_french_b2c = [
            t for t in in_country + eu_vat
            if t["tax_country"] == arg_country and t["vat_applied"] and not t["b2b"]
        ]
        cur_french_cards = ""
        if cur_french_b2c:
            cur_french_cards += _card("EU consumers, French VAT (HT)", format_amount(sum(ht_amount(t) for t in cur_french_b2c), currency))
            cur_french_cards += _card("TVA collected (B2C)", format_amount(sum(t["tax_amount"] for t in cur_french_b2c), currency))
        if show_converted and cur_french_b2c:
            cur_french_cards += _card(
                f"EU consumers, French VAT (HT, {arg_currency})",
                _sum_converted(convert, [dict(t, amount=ht_amount(t)) for t in cur_french_b2c], 'amount', arg_currency, format_amount)
            )
            cur_french_cards += _card(
                f"TVA collected (B2C, {arg_currency})",
                _sum_converted(convert, [dict(t, amount=t["tax_amount"]) for t in cur_french_b2c], 'amount', arg_currency, format_amount)
            )
        if cur_french_cards:
            currency_groups += _group("French VAT declaration", cur_french_cards)

        # Category tables
        tables = generate_category_section(
            [t for t in in_country if not t['b2b']], f"Domestic B2C ({arg_country})",
            format_amount, format_date, arg_currency, convert
        )
        tables += generate_category_section(
            [t for t in in_country if t['b2b']], f"Domestic B2B ({arg_country})",
            format_amount, format_date, arg_currency, convert
        )
        tables += generate_category_section(
            eu_vat, "Intra-EU transactions (with VAT)",
            format_amount, format_date, arg_currency, convert
        )
        tables += generate_category_section(
            eu_no_vat, "Intra-EU transactions (reverse-charged VAT)",
            format_amount, format_date, arg_currency, convert
        )
        tables += generate_category_section(
            outside_eu, "Extra-EU transactions",
            format_amount, format_date, arg_currency, convert
        )
        tables += generate_category_section(
            unknown, "Unknown transactions",
            format_amount, format_date, arg_currency, convert
        )

        # Refunds table
        refunds_table = ""
        if cur_refunds:
            refunds_converted_header = f"<th>Amount ({arg_currency})</th>" if show_converted else ""
            refund_rows = ""
            for i, t in enumerate(cur_refunds, start=1):
                converted_cell = ""
                if show_converted:
                    converted_cell = f'''
                    <td>{_converted_amount(convert, t['amount'], t['currency'], arg_currency, format_amount)}</td>'''
                refund_rows += f'''
                <tr>
                    <td>{i}</td>
                    <td>{format_date(t['date'])}</td>
                    <td class="amount-negative">{format_amount(t['amount'], t['currency'])}</td>{converted_cell}
                    <td>{t['currency']}</td>
                </tr>
'''
            refunds_table = f'''
    <div class="category-section">
        <div class="category-title">
            Refunded transactions - {len(cur_refunds)} transactions
        </div>
        <table>
            <thead>
                <tr>
                    <th>#</th>
                    <th>Date</th>
                    <th>Amount</th>{refunds_converted_header}
                    <th>Currency</th>
                </tr>
            </thead>
            <tbody>{refund_rows}
            </tbody>
        </table>
    </div>
'''

        tab_buttons += f'''
        <button class="tab-link" onclick="openTab(event, '{tab_id}')">{currency}</button>'''
        tab_contents += f'''
    <div id="{tab_id}" class="tab-content">
    <div class="summary">
        <h2>Summary - {currency}</h2>{currency_groups}
    </div>
    {_warnings_box([t for t in warned_all if t['currency'] == currency], format_date, format_amount)}
{tables}{refunds_table}
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
        .tab-bar {{
            display: flex;
            gap: 5px;
            margin: 20px 0;
            flex-wrap: wrap;
        }}
        .tab-link {{
            border: 1px solid #635bff;
            background-color: white;
            color: #635bff;
            padding: 8px 16px;
            border-radius: 20px;
            font-size: 0.95em;
            font-weight: 600;
            cursor: pointer;
        }}
        .tab-link:hover {{
            background-color: #f5f5ff;
        }}
        .tab-link.active {{
            background-color: #635bff;
            color: white;
        }}
        .tab-content {{
            display: none;
        }}
        .tab-content.active {{
            display: block;
        }}
    </style>
</head>
<body>
    <h1>Lavender Report</h1>
    <p><strong>Period:</strong> {start_date} to {end_date} | <strong>Default currency:</strong> {arg_currency} | <strong>Generated:</strong> {generated_at} (Europe/Paris)</p>

    <div class="tab-bar">{tab_buttons}
    </div>
{tab_contents}
    <script>
        function openTab(evt, tabId) {{
            document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
            document.querySelectorAll('.tab-link').forEach(el => el.classList.remove('active'));
            document.getElementById(tabId).classList.add('active');
            evt.currentTarget.classList.add('active');
        }}
    </script>
</body>
</html>
'''

    return html_content
