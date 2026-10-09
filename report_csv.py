"""CSV report generation for Lavender Report."""

from decimal import ROUND_HALF_UP, Decimal


def _converted(convert, amount, currency):
    """Converted amount as a display string, empty when no rate exists."""
    value = convert(amount, currency)
    return "" if value is None else f"{value:.2f}"


def _rate_lines(rates, arg_currency, rate_date, manual_rates):
    """Rate reference rows for the summary block."""
    lines = []

    def rate_row(currency):
        source = "manual" if currency in manual_rates else "douane.gouv.fr"
        return [f"Rate {currency} ({rate_date}, {source})", f"1 EUR = {rates[currency]} {currency}"]

    for currency in sorted(rates):
        if currency == "EUR":
            continue
        lines.append(rate_row(currency))
    if arg_currency != "EUR":
        lines.append(rate_row(arg_currency))
    return lines


def generate_csv_report(
    transactions_in_country, transactions_in_eu_with_vat, transactions_in_eu_without_vat,
    transactions_outside_eu, transactions_unknown_country, transactions_refunds,
    arg_country, arg_currency, format_date, convert, currency_stats, rates, rate_date, manual_rates
):
    """Generate CSV report of all transactions."""
    output = []

    # Write header
    output.append([
        "Date", "Type", "Amount", "Currency", f"Amount ({arg_currency})",
        f"Rounded Amount ({arg_currency})", "Country",
        "VAT Number", "VAT Applied", "Email", "Status", "Fees", "Category"
    ])

    def rounded_amount(t):
        """Whole-unit rounding in the default currency: of the converted
        amount for non-default currencies, of the native amount otherwise.
        Empty when no rate is available."""
        if t['currency'] == arg_currency:
            return f"{int(Decimal(str(t['amount'])).quantize(0, ROUND_HALF_UP))}"
        converted = convert(t['amount'], t['currency'])
        if converted is None:
            return ""
        return f"{int(Decimal(str(converted)).quantize(0, ROUND_HALF_UP))}"

    def category_rows(transactions, category_label):
        rows = []
        for t in transactions:
            rows.append([
                format_date(t['date']),
                "Payment",
                f"{t['amount']:.2f}",
                t['currency'],
                _converted(convert, t['amount'], t['currency']),
                rounded_amount(t),
                t['country'],
                t['vat_number'],
                "Yes" if t['vat_applied'] else "No",
                t['email'],
                t['status'],
                f"{t['fee']:.2f} {t['currency']}",
                category_label
            ])
        return rows

    def category_totals(transactions, label):
        """One total row per currency (never mixed), plus the converted
        combined total in the default currency."""
        rows = []
        totals = {}
        for t in transactions:
            totals[t['currency']] = totals.get(t['currency'], 0) + t['amount']
        for currency in sorted(totals):
            rows.append([
                f"{label} Total ({currency})", "",
                f"{totals[currency]:.2f}", currency, "", "", "", "", "", "", "", "", ""
            ])
        converted_total = None
        converted_sum = 0
        for t in transactions:
            value = convert(t['amount'], t['currency'])
            if value is None:
                converted_total = None
                break
            converted_sum += value
        else:
            converted_total = converted_sum
        if converted_total is not None and len(totals) > 1:
            rows.append([
                f"{label} Total (converted to {arg_currency})", "",
                f"{converted_total:.2f}", arg_currency, "", "", "", "", "", "", "", "", ""
            ])
        return rows

    categories = [
        (transactions_in_country, f"Domestic ({arg_country})"),
        (transactions_in_eu_with_vat, "Intra-EU (with VAT)"),
        (transactions_in_eu_without_vat, "Intra-EU (reverse-charged VAT)"),
        (transactions_outside_eu, "Extra-EU"),
        (transactions_unknown_country, "Unknown"),
    ]
    for transactions, label in categories:
        output.extend(category_rows(transactions, label))
        output.extend(category_totals(transactions, label))

    # Add refunds
    for t in transactions_refunds:
        output.append([
            format_date(t['date']),
            "Refund",
            f"{t['amount']:.2f}",
            t['currency'],
            _converted(convert, t['amount'], t['currency']),
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "Refund"
        ])

    # Add summary rows: per currency, then converted combined
    output.append([])
    output.append(["SUMMARY"])
    for currency in sorted(currency_stats):
        stats = currency_stats[currency]
        output.append([f"Payments ({currency})", f"{stats['payments']}", f"{stats['payments_total']:.2f} {currency}"])
        if stats['refunds']:
            output.append([f"Refunds ({currency})", f"{stats['refunds']}", f"{stats['refunds_total']:.2f} {currency}"])
        if stats['fees']:
            output.append([f"Stripe Fees ({currency})", f"{stats['fees']:.2f} {currency}"])
            if stats['addon_fees']:
                output.append([f"Add-on Stripe Fees ({currency})", f"{stats['addon_fees']:.2f} {currency}"])
    # Combined totals converted to the default currency: payments and
    # refunds are converted per transaction (matching the rows above);
    # fees cover fee-only balance transactions too and are converted
    # per currency
    all_transactions = (
        transactions_in_country + transactions_in_eu_with_vat + transactions_in_eu_without_vat +
        transactions_outside_eu + transactions_unknown_country
    )
    output.append([f"Combined Total Payments (converted to {arg_currency})", _sum_converted_txs(convert, all_transactions)])
    output.append([f"Combined Total Refunds (converted to {arg_currency})", _sum_converted_txs(convert, transactions_refunds)])
    output.append([f"Combined Stripe Fees (converted to {arg_currency})", _sum_converted(convert, currency_stats, 'fees')])
    output.extend(_rate_lines(rates, arg_currency, rate_date, manual_rates))

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


def _sum_converted_txs(convert, transactions):
    """Converted total over transactions, each conversion rounded to the
    cent (matching the converted column rows); empty string when some
    currency has no rate."""
    total = 0
    for t in transactions:
        converted = convert(t['amount'], t['currency'])
        if converted is None:
            return ""
        total += converted
    return f"{total:.2f}"


def _sum_converted(convert, currency_stats, field):
    """Sum a per-currency stat converted to the default currency; empty
    string when some currency has no rate."""
    total = 0
    for currency, stats in currency_stats.items():
        if not stats.get(field):
            continue
        value = convert(stats[field], currency)
        if value is None:
            return ""
        total += value
    return f"{total:.2f}"
