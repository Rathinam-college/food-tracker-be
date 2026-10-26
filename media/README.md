# Media & Document Storage

This directory contains uploaded documents, bill attachments, and asset media organized by topic:

## Directory Structure by Topic

* **`pr_receipts/`**: Purchase Request receipt attachments, goods inward delivery challans, and signed vendor bills uploaded by storekeepers during material receipt verification.
  * Format: `pr_receipts/<YYYYMMDD>_<PR-REF>/<filename>`
* **`opex_bills/`**: Operational Expenditure (OPEX) bill attachments and invoice receipts uploaded during payment entry and settlement.
  * Format: `opex_bills/<YYYYMMDD>_<OPEX-NO>/<filename>`
* **`vendor_documents/`**: Vendor statutory and compliance documents (e.g., FSSAI licenses, GST certificates, agreements).
  * Format: `vendor_documents/<filename>`
* **`branding_and_assets/`**: Logos, icons, and official institutional media.

## Production Server Access
When deployed to production:
* Media is mounted to `/app/bills` in Docker and served at `/food_tracker/bills/`.
* On the production server host, files are stored at `~/food-tracker-prod/bills/`.
