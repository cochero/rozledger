# RozLedger Controlled Beta Launch Checklist

Last updated: 2026-06-07

## Beta Goal

Validate that real small-business users can create an account, set up their business, create a professional invoice, download or share it, record payment, record expenses or vendor bills, and understand their dashboard without support.

## Invite Size

- Start with 5 trusted users.
- Do not invite more than 10 users until all blocker issues from the first 5 are fixed.
- Use businesses from different categories: service, trading, travel, consulting, and US service.

## Entry Criteria

- Public home, pricing, contact and content pages load for `rozledger.in` and `rozledger.com`.
- Signup/login works with password visibility and CAPTCHA on registration.
- Business profile saves company name, phone, address, logo/payment details and template.
- Invoice creation supports business phone, client phone, full addresses, quantity, rate, tax on/off, bank/payment note and thank-you note.
- Invoice web preview and PDF render professionally.
- Payment received can be posted against a selected invoice.
- Vendor bill and vendor payment flows work.
- Reports page shows P&L, AR aging, AP aging and cash summary.
- Audit trail/search/reconciliation pages load.
- Admin can approve Pro trial and see request dates, activation date and expiry date.
- Backups are running before deployment changes.

## Beta User Tasks

Each beta user should complete these tasks:

1. Create account and login.
2. Open dashboard and identify the next action without support.
3. Create or edit business profile.
4. Create one invoice with at least two line items.
5. Open invoice preview and download PDF.
6. Record a partial payment against the invoice.
7. Record a vendor bill or expense.
8. Open reports and confirm AR/AP totals make sense.
9. Open customer or vendor ledger statement.
10. Send feedback on what was confusing, slow or missing.

## Internal QA Accounts

Use clearly marked QA accounts so test records do not get confused with customer data.

- `beta-india-service@rozledger.test`
- `beta-india-trading@rozledger.test`
- `beta-india-travel@rozledger.test`
- `beta-us-service@rozledger.test`
- `beta-india-consulting@rozledger.test`

## Success Metrics

- At least 4 of 5 beta users can create invoice and PDF without help.
- At least 3 of 5 can record payment against invoice without help.
- At least 3 of 5 understand dashboard next actions.
- No blocker bug in signup, login, invoice save, PDF, payment receipt, vendor bill, or dashboard.
- No market mix-up: `.com` must not show GST/UPI-first workflows; `.in` must keep India GST/UPI workflows.

## Stop Conditions

Pause new invites if any of these occur:

- Login or signup fails for more than one user.
- Invoice PDF is broken or visually unusable.
- Payment posting does not update invoice balance.
- Vendor bill/payment posting creates wrong AP balance.
- Any user sees another user's data.
- Public pages show inaccurate pricing, contact or tax copy.

## Support Script

Use this message when inviting testers:

> Hi, we are testing RozLedger with a small beta group. Please create a test account, add your business profile, create one invoice, download the PDF, record one payment and try one expense/vendor bill. Please tell us exactly where you felt confused or blocked. This is a beta, so please do not use it yet for official tax filing or final statutory records.

## Beta Feedback Questions

- Could you create an invoice without help?
- Did the invoice PDF look professional enough to send to your customer?
- Was payment received easy to record?
- Was business profile setup clear?
- Was anything confusing on the dashboard?
- What one feature would make you use this daily?
- Would you pay for this at the listed price?

## Go/No-Go Decision

Move from controlled beta to wider public invite only when:

- All entry criteria pass.
- No stop condition is open.
- At least 5 complete customer journeys are tested.
- All blocker feedback from first beta users is fixed or intentionally deferred.
