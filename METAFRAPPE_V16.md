# Metafrappe Frappe v16 branch

This `version-16` branch starts from `frappe/studio` develop commit
`bbb160152c4de76aec618228015b966aba95956f`.
Studio remains early-development software. This fork does not change that status.

## Compatibility change

LiteLLM 1.83.7 requires Click 8.1.8, which conflicts with the deployed Frappe
16.33.1 requirement Click 8.4.x. LiteLLM 1.95.0 permits Click 8.x and matches
Frappe Builder's requirement. CI resolves the combined requirements of
Metaframer's existing 19 apps plus Drive, Studio and Builder.

The branch targets Frappe 16 and Python 3.14. CI uses the deployed Frappe
revision, Node 24 and MariaDB 10.6.25. It checks fresh installation, migration,
production assets, app/page creation, draft/publish workflows, guest access
restrictions and a real application bundle build.

The AI adapter is exercised through LiteLLM's mock-response path with network
transport blocked. Paid provider responses and credentials are not tested.
Configure an AI API key in Studio Settings when enabling the AI assistant on a site.

## Press installation

Add `https://github.com/metafrappe/studio`, branch `version-16`, to the existing
Metaframer v16 Apps group after Drive and after its v16 compatibility CI is green.
Install on individual sites separately as needed. Keep production security settings
at their defaults; CI does not require disabling CSRF or enabling developer mode.

Upstream's exported-app development workflow still requires a development
environment. Re-run the shared dependency and runtime checks before merging
subsequent upstream changes.
