# Security Policy

> **PL:** Luki bezpieczeństwa zgłaszaj prywatnie (niżej), nie w publicznych zgłoszeniach.
> Zgłoszenia po polsku i po angielsku są mile widziane.

Spółdzielnia AI stores private notes and shared knowledge of a community and protects that
knowledge from being copied out through the chat. Security reports are taken seriously and
handled privately until a fix is available.

## Reporting a vulnerability

**Do not open a public issue, pull request or discussion for a security problem.**

1. Preferred: use GitHub's private reporting — the **Security** tab of this repository →
   **Report a vulnerability**.
2. Alternatively, e-mail **piotr@pacewi.cz** with the subject `[SECURITY] Spółdzielnia AI`.

Please include:

- what is affected (component, endpoint or page) and the commit you tested;
- steps to reproduce, or a proof of concept, against your **own** deployment;
- the impact you expect (whose data, which rule below is broken);
- whether you want to be credited, and under which name.

## What happens next

This is a small, volunteer-maintained project, so the times below are goals, not guarantees:

| Step | Target |
|---|---|
| Acknowledgement of your report | within 5 working days |
| First assessment (confirmed or not, severity) | within 14 days |
| Fix or mitigation for a confirmed high or critical issue | within 90 days |

You will be kept informed. Once a fix is released, the issue is published as a GitHub security
advisory, with credit to you unless you prefer otherwise. Please do not disclose the issue
publicly before then; if 90 days pass without a fix, we will agree on a disclosure date together.

## Supported versions

There are no tagged releases yet. Only the latest commit on `main` receives security fixes.

## Scope

The most valuable reports break one of the project's architectural rules
(see [`AGENTS.md`](AGENTS.md), section 3, and [`docs/SECURITY.md`](docs/SECURITY.md)):

- **Isolation:** reading or changing another user's private notes, bypassing Row-Level Security.
- **Anti-distillation:** getting raw stored chunks, verbatim articles or bulk knowledge out of the
  chat or API past `AntiDistillationGuard` (including through translation or other formats).
- **Provenance and consent:** content of an author who withdrew or suspended consent still being
  used in answers.
- **Private data leaving approved providers:** private notes, drafts, images or their embeddings
  sent to a model provider that is not on the approved list.
- **Authentication and authorisation:** bypassing login, MFA step-up, roles, CSRF or rate limits.
- **Input handling:** SSRF, malicious uploads getting past validation or scanning, XSS or CSP
  bypass, prompt injection that crosses one of the boundaries above.

Out of scope:

- the development stack (`compose.dev.yaml`, `make mfa-off`, dev users and generated dev secrets);
- issues that require an already compromised server, administrator account or model provider;
- denial of service by sheer traffic volume, and social engineering;
- quality of answers from a language model that do not leak protected data;
- reports from automated scanners without a demonstrated impact.

## Testing rules

Test only against a deployment you run yourself (`make up` starts a complete local stack).
Never test against someone else's server, and never access, keep or share real users' data.
Good-faith research that follows this policy will not be pursued legally by the maintainers.

## For deployers

Known, accepted risks of each stage are listed in
[`docs/SECURITY_REVIEW.md`](docs/SECURITY_REVIEW.md). Run `make audit` (dependency, code,
secret and image scanning) before each deployment, and keep the server updated.
