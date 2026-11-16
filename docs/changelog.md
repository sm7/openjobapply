# Changelog

This file lists the notable changes to OpenJobApply.

## Initial public release

This is the first public version of OpenJobApply.

It has these main features.

- Job discovery and scoring. The system finds jobs and scores them against the candidate profile.
- ATS adapters. Each adapter handles the forms of one applicant tracking system.
- Reservation and receipt ledger. A job counts as applied only after an employer receipt.
- Pacing gates. The gates limit how fast the worker applies.
- Dashboard. It has the pages Overview, Applied jobs, Skipped jobs, Manual jobs and Live logs.
- Local user service. A launchd or systemd user service runs the supervisor and the dashboard.
