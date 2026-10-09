# LavenderReport

**LavenderReport** is an open-source command-line tool for categorizing and reporting Stripe payment transactions by region. Designed for EU businesses, it helps you split your payment data into domestic, intra-EU, and extra-EU transactions for easy VAT reporting.

## Features
- Categorize payments by:
  - Domestic transactions (your company’s country)
  - Intra-EU transactions
  - Extra-EU transactions
- Simple CLI interface
- Works with your Stripe API key
- Easy-to-read regional payment breakdowns
- Export reports to CSV or HTML format

## Requirements
- Python 3.x
- uv

## Configuration

Create a `.env` file in the root directory with the following content:

```
STRIPE_SECRET_KEY=your_stripe_secret_key_here
```

## Usage

To generate a report for a specific month, run the following command:

`uv run main.py [--country COUNTRY] [--year YEAR] [--month MONTH] [--export {csv,html}] [--output FILENAME]`

### Command-Line Arguments

| Argument | Type | Description | Default |
|----------|------|-------------|---------|
| `--country` | str | Country code for domestic transactions | FR |
| `--year` | int | Year to report on | Current year |
| `--month` | int | Month to report on | Previous month |
| `--export` | str | Export format: `csv` or `html` | None (console output) |
| `--output` | str | Output filename for export | Auto-generated |
| `--currency` | str | Report currency; transactions in other currencies are listed separately | EUR |
| `--locale` | str | Number formatting for display: `en` (3571.65 EUR) or `fr` (3 571,65 €) | en |
| `--debug` | flag | Write a full balance-transaction log to `debug_<period>.txt` (all transactions, skipped ones, per-currency fees) | off |

### Caching

Per-charge details (customer, invoice, tax rates) are immutable, so they are cached in `.cache/charge_details.json` after the first run of a period. Reruns only refetch the balance-transaction list. Delete the `.cache/` directory to force a full refetch.

### Classification and warnings

The transaction country is resolved from the tax rate applied on the invoice, so the categories mirror what was actually charged and stay consistent with your invoicing; the customer's billing address is used as a fallback when the invoice carries no tax rate. The report flags, in the console summary and the CSV/HTML exports:

- billing address country not matching the tax rate applied on the invoice
- VAT number prefix not matching the country
- reverse-charged EU sales without a customer VAT number

These are advisory: the categories still reflect your Stripe invoicing, but flagged rows should be reviewed before declaring VAT.

This will generate a categorized report with the following sections:
- Domestic
- Intra-EU
- Extra-EU

### Examples

**Generate console report:**
```bash
uv run main.py --country FR --year 2025 --month 05
```

**Export to CSV:**
```bash
uv run main.py --country FR --year 2025 --month 05 --export csv
```

**Export to CSV with custom filename:**
```bash
uv run main.py --country FR --year 2025 --month 05 --export csv --output report.csv
```

**Export to HTML (French number formatting):**
```bash
uv run main.py --country FR --year 2026 --month 9 --locale fr --export html --output report.html
```

**Debug a period (full balance-transaction log):**
```bash
uv run main.py --year 2026 --month 9 --debug
```

## Export Formats

The tool supports exporting reports in two formats:

### CSV Export
- Includes all transaction details in a structured format
- Columns: Date, Type, Amount, Currency, Rounded Amount, Country, VAT Number, VAT Applied, Email, Status, Fees, Category
- Includes summary totals at the end
- Suitable for spreadsheet analysis

### HTML Export
- Beautiful, responsive web page with modern styling
- Color-coded amounts (green for payments, red for refunds)
- Summary dashboard grouped into Activity, Stripe fees and VAT categories
- Country flags next to country codes
- Warning box highlighting possible VAT classification mismatches
- Organized by transaction categories
- Easy to share and view in any web browser

## Contributing

We welcome contributions! If you would like to improve **LavenderReport**, feel free to fork the project and submit a pull request.

## License

This project is licensed under the GNU GPL v3 License - see the [LICENSE](LICENSE) file for details.
