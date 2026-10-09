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

`uv run main.py [--country COUNTRY] [--year YEAR] [--month MONTH] [--export {csv,html}] [--output FILENAME] [--currency CUR] [--fx-rate CUR=RATE] [--locale {en,fr}] [--debug]`

### Command-Line Arguments

**Period and scope**

| Argument | Type | Description | Default |
|----------|------|-------------|---------|
| `--country` | str | Your company's two-letter ISO country code, used for the domestic category and the French VAT declaration | FR |
| `--year` | int | Year to report on | Year of the previous month |
| `--month` | int | Month to report on | Previous month |

**Output**

| Argument | Type | Description | Default |
|----------|------|-------------|---------|
| `--export` | str | Export format: `csv` or `html` | None (console output) |
| `--output` | str | Output filename for export | `lavender_report_<period>_<generation timestamp>.csv/html` |
| `--locale` | str | Number formatting for display: `en` (3571.65 EUR) or `fr` (3 571,65 €) | en |
| `--debug` | flag | Write a full balance-transaction log to `debug_<period>.txt` (all transactions, skipped ones, per-currency fees) | off |

**Currencies**

| Argument | Type | Description | Default |
|----------|------|-------------|---------|
| `--currency` | str | Default report currency. All currencies are reported; non-default ones are converted at the official douane.gouv.fr monthly rate | EUR |
| `--fx-rate` | str | Manual exchange rate for a currency, douane direction (`1 EUR = RATE CUR`, e.g. `USD=1.16`); repeatable, takes precedence over douane.gouv.fr | None |

See [Multiple currencies](#multiple-currencies) for how conversion and rounding work.

### Caching

Per-charge details (customer, invoice, tax rates) are immutable, so they are cached in `.cache/charge_details.json` after the first run of a period. Reruns only refetch the balance-transaction list. Delete the `.cache/` directory to force a full refetch.

Fetched exchange rates are cached in `.cache/fx_rates.json`; published douane.gouv.fr rates never change, so they are not refetched.

### Multiple currencies

All currencies with activity are classified and reported, never mixed: totals are kept per currency. Non-default currencies are converted to the default currency with the official monthly rate from [douane.gouv.fr](https://www.douane.gouv.fr/debweb/cf.srv?etape=menuTaux&) (the rate applicable on the first day of the reported month, covering the whole month), which is the rate usable for French VAT declarations. If a rate cannot be fetched (unknown currency, network failure), pass it manually with `--fx-rate CUR=RATE`; amounts in currencies without a rate stay in their original currency and are excluded from converted totals, with a visible warning.

The French VAT declaration figures (EU consumers with French VAT, domestic B2C/B2B HT bases) include foreign-currency sales converted at the official rate, since declarations are filed in the default currency.

Converted amounts are computed and rounded to the cent **per transaction**, so the figures shown in the reports reconcile: category totals, combined totals and the declaration figures all sum the same per-row conversions. Stripe fees are the one exception: they are converted per currency, because the fee totals include fee-only balance transactions that appear in no table.

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

**Multi-currency report with a manual exchange rate** (e.g. offline, or a currency douane.gouv.fr does not cover):
```bash
uv run main.py --year 2026 --month 9 --export html --fx-rate USD=1.16
```

**Report with a different default currency** (per-currency tabs and conversions are then expressed in that currency):
```bash
uv run main.py --year 2026 --month 9 --export html --currency USD
```

**Debug a period (full balance-transaction log):**
```bash
uv run main.py --year 2026 --month 9 --debug
```

## Export Formats

The tool supports exporting reports in two formats:

### CSV Export
- Includes all transaction details in a structured format
- Columns: Date, Type, Amount, Currency, Amount (default currency, converted at the official rate), Rounded Amount, Country, VAT Number, VAT Applied, Email, Status, Fees, Category
- Per-category and per-currency totals, plus converted combined totals and the rates used
- Suitable for spreadsheet analysis

### HTML Export
- Beautiful, responsive web page with modern styling
- One tab per currency plus an Overview tab with the whole situation (per-currency activity, converted combined totals, converted VAT categories and French VAT declaration)
- Tables show the converted amount column for non-default currencies
- Color-coded amounts (green for payments, red for refunds)
- Summary dashboard grouped into Activity, Stripe fees and VAT categories
- Country flags next to country codes
- Warning box highlighting possible VAT classification mismatches
- Organized by transaction categories
- Easy to share and view in any web browser

## Contributing

We welcome contributions! If you would like to improve **LavenderReport**, feel free to fork the project and submit a pull request.

The codebase is linted with [ruff](https://docs.astral.sh/ruff/); run `uv run ruff check .` before submitting.

## License

This project is licensed under the GNU GPL v3 License - see the [LICENSE](LICENSE) file for details.
