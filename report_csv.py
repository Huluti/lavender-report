"""CSV report generation for Lavender Report."""

from decimal import ROUND_HALF_UP, Decimal


def generate_csv_report(
    transactions_in_country, transactions_in_eu_with_vat, transactions_in_eu_without_vat,
    transactions_outside_eu, transactions_unknown_country, transactions_refunds,
    nb_payments, total_payments, total_fees, addon_fees, nb_refunds, total_refunds,
    arg_country, format_date
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
