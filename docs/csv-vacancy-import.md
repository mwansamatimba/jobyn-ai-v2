# Vacancy CSV import

Use [`templates/vacancy-import-template.csv`](templates/vacancy-import-template.csv)
as the UTF-8 CSV header template. UTF-8 with BOM is also accepted. Required columns
are `title`, `company`, `location`, `description`, `source_name`, `source_url`, and
`application_url`. Optional columns are `external_id`, `country`, `province`,
`city`, `remote_eligibility`, `requirements`, `posted_at`, `deadline`, `category`,
and `attribution`. Dates must use ISO-8601 date or datetime values.

The importer recognizes explicit aliases including `job_title`, `position`,
`company_name`, `employer`, `job_location`, `job_description`, `source`,
`listing_url`, `apply_url`, `job_id`, `region`, `qualifications`, `date_posted`,
`application_deadline`, and `source_attribution`. Other headers are ignored and
reported as unmapped. An operator can pass a header-to-field mapping to
`preview_csv_vacancies` or `import_csv_vacancies`; ambiguous mappings and
candidate/profile/credential columns are rejected.

`CSV_IMPORT_MAX_FILE_SIZE_BYTES` defaults to 5 MiB (validated maximum 50 MiB).
`CSV_IMPORT_MAX_ROWS` defaults to 500 rows (validated maximum 10,000). The
workflow requires a valid non-production `C5ExperimentConfig`, which accepts only
an isolated SQLite database. Preview is read-only. Import records a batch ID,
summary, source links, and observations in that SQLite store, and the batch write
is transactional. Repeated rows are deduplicated using the existing normalized
job identity; changed records update the canonical row while absent rows are
not deleted. Application URLs are retained as metadata and never fetched.

Only existing inactive, permission-required C5 experiment sources with a
configured namespace are accepted. `zambian_public_institution` currently needs
an institution identifier that is not part of this CSV template, so those rows
are rejected. This does not grant source permission or make imported jobs
visible to the production matching backend.

The administrator interface is served at `/admin`. Its authenticated API is:

- `POST /api/v1/admin/jobs/csv/preview` — validates the upload without writes.
- `POST /api/v1/admin/jobs/csv/import` — imports only the exact file and column
  mapping represented by a signed preview token; tokens expire after 30 minutes.
  Replaying the same token returns the original batch result and does not add
  duplicate observations or batch rows; a new preview creates a new confirmation.
- `GET /api/v1/admin/jobs/csv/imports` — lists isolated batch summaries.
- `GET /api/v1/admin/jobs/csv/imports/{batch_id}` — shows one batch summary.

Every API route requires an active account whose persisted `users.role` is
`admin`. The existing role migration provides the field; an authorized operator
must provision administrator roles outside this workflow. Registration,
email addresses, and the admin page itself do not grant administrator access.
The UI enforces a 5 MiB upload and 500-row limit; these caps cannot be raised
through application settings. The ASGI boundary limits the complete multipart
body before parsing. Preview returns safe row summaries and an HMAC-signed
confirmation token bound to the exact CSV bytes, mapping, and experiment; import
rejects stale or changed submissions. The token also binds the sanitized
filename and resolved staging path. The server requires
`C5_EXPERIMENT_ID`, `C5_ENVIRONMENT=non_production`,
`C5_EXPERIMENT_MODE=true`, and an isolated SQLite `C5_DATABASE_URL`. It rejects
an SQLite staging path that aliases the application database. No production
promotion or production matching visibility is implemented.
