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

## Requirements
- Python 3.x
- uv

## Usage

To generate a report for a specific month, run the following command:

`uv run main.py [--country COUNTRY] [--year YEAR] [--month MONTH]`

This will generate a categorized report with the following sections:
- Domestic
- Intra-EU
- Extra-EU

### Example

`uv run main.py --country FR --year 2025 --month 05`

## Contributing

We welcome contributions! If you would like to improve **LavenderReport**, feel free to fork the project and submit a pull request.

## License

This project is licensed under the GNU GPL v3 License - see the [LICENSE](LICENSE) file for details.
