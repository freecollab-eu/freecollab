# Customer-owned SCIM 2.0 provisioning

SCIM provisions organization-managed people and groups into one customer workspace. It never matches or takes over a global personal account by email. Configure the customer's OIDC provider and choose a compatible **Integrations → People** onboarding policy first; [WORKFORCE_IDENTITY.md](WORKFORCE_IDENTITY.md) explains identifier matching and separate managed identities.

## Connect the customer's directory

1. Sign in as a native workspace **owner**. Open **Integrations → People → Directory provisioning**.
2. Create the provisioning credential using the native password and local MFA when enabled. Copy the returned token privately before dismissing it. It is shown once; the server stores only its SHA-256 digest.
3. Configure the customer's SCIM client with the displayed HTTPS endpoint and bearer token. The endpoint is `https://collab.example.org/api/scim/v2/{opaque_directory_id}`. It is not a global directory endpoint.
4. Map `externalId` to the signed OIDC claim selected in **Provisioned identity identifier**. Provision a test person, then verify their actual organization sign-in.
5. Push groups, then configure explicit workspace-role and channel grants in FreeCollab. Group names or IdP claims do not grant privileges by themselves.

The customer owns the directory configuration and credential. No installation-wide SCIM secret is required. The configured public HTTPS origin determines resource locations; the normal deployment must provide TLS. These local tests do not provision a public TLS endpoint or connect a real directory tenant.

Owners alone create or rotate the token because it delegates management of owner-approved directory role rules. Workspace admins can inspect configuration/audit metadata and disable the credential. Native password/MFA confirmation protects credential and mapping changes. Customer-scoped SSO sessions cannot enter these global account-security administration flows.

Each credential records its actual issuing owner separately from the person who first opened the directory settings. Authentication and mutations recheck that issuer is an existing, unblocked **current workspace owner**. Demotion, removal or blocking invalidates the token; a current owner must rotate it. The UI exposes `credential.requires_rotation` for this case. Ordinary owner password rotation or logout is separate from explicitly rotating this service credential.

Rotation immediately replaces the old digest; previously copied tokens stop authenticating. Disabling provisioning clears the credential. Disabling or rotating this connector does not deactivate all previously provisioned people: send `active:false`, remove workspace membership or change the relevant account controls when offboarding.

## Supported service contract

Send `Authorization: Bearer <workspace-token>` with every SCIM request. JSON responses use `application/scim+json`, include SCIM schemas, and are not cached. A SCIM token cannot authenticate the native user API, another directory's endpoint or workspace administration.

Relative to the displayed directory endpoint:

| Resource | Supported operations |
| --- | --- |
| `/ServiceProviderConfig` | `GET`; declares patch, filter and ETag support; no bulk, sorting or password-change support |
| `/Schemas`, `/Schemas/{schema_id}` | `GET`; core User/Group and the supported enterprise User extension |
| `/ResourceTypes`, `/ResourceTypes/User`, `/ResourceTypes/Group` | `GET` |
| `/Users` | `GET` with filtering/pagination; `POST` managed provisioning |
| `/Users/{id}` | `GET`, `PUT`, `PATCH`, `DELETE` |
| `/Groups` | `GET` with filtering/pagination; `POST` |
| `/Groups/{id}` | `GET`, `PUT`, `PATCH`, `DELETE` |

`startIndex` is one-based. `count` is 0–100, default 100; `count=0` returns the matching total without resources. List responses include `totalResults`, `startIndex`, `itemsPerPage` and `Resources`. Requests are limited to 1 MiB, group membership to 2,000 user references, and PATCH to 100 operations. A directory retains at most 10,000 user records, including soft-deleted records, and 2,000 groups. Native mapping configuration accepts at most 200 group rules with 100 channel grants per rule. Authentication uses persistent request throttling.

Filtering supports one scalar equality expression, not arbitrary SCIM logical expressions:

```text
userName eq "elin@customer.example"
externalId eq "provider-subject-123"
id eq "<SCIM-resource-id>"
active eq true
displayName eq "Engineering"
```

User filters accept `userName`, `externalId`, `id`, `active`; group filters accept `displayName`, `externalId`, `id`. User names and group display names compare case-insensitively. External identifiers compare exactly. Unsupported filters and malformed escaped literals return SCIM HTTP400 `invalidFilter`, rather than being executed as queries or treated as matches.

## Provision and maintain people

Example request body for `POST /Users`:

```json
{
  "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
  "externalId": "provider-subject-123",
  "userName": "elin@customer.example",
  "displayName": "Elin Dahl",
  "active": true,
  "emails": [{"value": "elin@customer.example", "type": "work", "primary": true}]
}
```

The response returns a SCIM resource ID, metadata location and ETag. That resource is associated with a separate managed internal user; its work email does not reserve, verify or recover a personal account. Two workspaces may provision the same `userName`/`externalId` independently. A duplicate create for the same existing pair returns that existing resource; conflicting reuse of either name or external ID returns HTTP409 `uniqueness`.

Supported profile fields include `userName`, `externalId`, `displayName`, `name`, `emails`, `nickName`, `title`, `userType`, `preferredLanguage`, `locale` and `timezone`. The enterprise extension supports `employeeNumber`, `costCenter`, `organization`, `division`, `department` and `manager.value`. These are directory metadata, not authorization grants. Passwords, nonempty privilege roles and entitlements are not synchronized through User writes.

User PATCH supports `add`, `replace`, `remove`, a pathless value object, the supported scalar profile fields, `name.givenName`, `name.familyName`, `name.formatted`, `name.middleName`, and enterprise-extension paths for `employeeNumber`, `costCenter`, `organization`, `division`, `department`. `userName` and `active` cannot be removed. Whole `name`/`emails` values can be replaced; arbitrary filtered email subpaths and manager-specific PATCH subpaths are not implemented. Unsupported paths return HTTP400 `invalidPath`. PATCH validates the complete request before applying changes.

`PUT` replaces the supplied profile under the supported schema. Conditional writes may send the returned weak ETag in `If-Match`, or `*`; a stale version returns HTTP412. Do not generate replacement IDs or treat a failed precondition as permission to overwrite a newer resource.

An exact provider subject already linked to a personal account is not silently converted by SCIM. Subject-based provisioning rejects that conflict. Other configured claim matches are also checked during OIDC sign-in so an ambiguous personal binding cannot bypass a disabled managed principal. Migrate explicitly; do not resolve this by assigning another person's email or changing a provider subject.

## Groups and access rules

Groups contain User resource references such as `{ "value": "<SCIM-user-id>" }`. Cross-workspace references are rejected. Nested groups are not implemented. Group PATCH supports whole-object updates, `displayName`, `externalId`, `members` add/replace/remove, and removal through `members[value eq "<id>"]`.

In People, select a default provisioned role (`member` or `guest`) and explicit group mappings. The effective managed workspace role is the strongest applicable base/group grant. For a directory where unassigned people should remain guests, choose a guest default and grant member access through the intended staff groups. A guest mapping does not downgrade a stronger explicit/base member grant.

Only a native owner can create, change or remove a mapping granting the workspace `admin` role. A native workspace admin may preserve an unchanged owner-approved admin mapping while editing other authorized rules. No mapping grants workspace `owner` or global installation roles. Native owners also cannot promote managed principals to workspace owners through the ordinary member-role API.

Every changed channel grant requires the saving administrator to be a current channel admin as well as an active native workspace owner/admin. Workspace ownership alone does not open a private channel. Direct-message membership is not group-mappable.

Channel mappings retain their native approver. Each provisioning reconciliation rechecks that person's active workspace-admin/owner and channel-admin grants. A stale approval cannot admit new recipients; tracked SCIM-only access is removed through the remote-first path. `channels[].requires_reapproval` identifies such mappings. An administrator who currently holds both required permissions can confirm and save the rule again to renew its approval. The directory-token owner's role does not substitute for a private-channel approver.

Removing a group or rule removes its tracked SCIM grants. Preexisting explicit channel membership is preserved, including restoration of its prior role after a temporary SCIM elevation. Workspace role reconciliation likewise retains explicit native grants rather than replacing every membership wholesale. Secure channel membership still uses the normal trusted-device requirements; group assignment does not verify cryptographic devices.

## Offboarding, failure and reactivation

Use a User PATCH such as:

```json
{
  "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
  "Operations": [{"op": "replace", "path": "active", "value": false}]
}
```

Offboarding first removes the managed recipient from encrypted rooms, then commits its inactive state, target-workspace membership removal and scoped-session revocation. It removes channel access and fails subsequent SSO, REST, socket and Matrix authorization. It does not block an unrelated personal user with the same email or revoke that person's other workspaces/sessions.

If remote revocation fails, the operation returns an error and does not commit a local-only success. A retry can complete removal. New encrypted invitations are attempted after the native grant commits; a temporary failure records `encrypted_invitation_pending` in the audit rather than claiming remote delivery. Native membership and remote device trust remain separate. An authorized channel member can retry `POST /api/secure/channels/{channel_id}/sync`; inspect the audit when the private Matrix service is unavailable.

`active:true` reactivates the same managed identity and workspace membership, then applies current group rules. It does not restore arbitrary old group-channel grants that are no longer configured. Canonical direct-message records/history are retained under the ordinary workspace access rules. Reactivation does not undo prior session revocation or recover encryption keys.

`DELETE /Users/{id}` is soft removal for access and directory visibility. Native identity/history and required keys are retained; it is not account erasure. Group references to that deleted resource are removed. Reprovisioning the same retained user name/external-ID pair reuses its managed identity rather than silently merging or creating a replacement personal account. Deleting a group reconciles its grants without deleting messages/files.

No offboarding operation can retract content or keys already stored on a device. Permanent retention/deletion is a separate policy and workflow described in [AUTH_SESSIONS.md](AUTH_SESSIONS.md).

## Native administration API

These endpoints include `/api` and require an unrestricted native workspace owner/admin session. Writes require `{password, code?}`; credential creation/rotation additionally requires an owner.

| Operation | Endpoint |
| --- | --- |
| Directory status, endpoint, safe credential metadata and counts | `GET /api/workspaces/{id}/scim` |
| Create/rotate one-time bearer token | `POST /api/workspaces/{id}/scim/token` |
| Disable provisioning credential | `POST /api/workspaces/{id}/scim/disable` |
| Read/save group mappings | `GET` / `PUT /api/workspaces/{id}/scim/mappings` |
| Recent audit events | `GET /api/workspaces/{id}/scim/audit` |

Mapping writes contain `mappings: [{group_id, workspace_role, channels: [{channel_id, role}]}]` and optional `default_role`. Send only those writable fields; `group_name` and `requires_reapproval` are response metadata. The mapping set is a replacement, so preserve unchanged rules the administrator intends to retain. Reads return only eligible channel choices; secure channel titles remain client-encrypted. Audit records contain actions/resource IDs/times and do not return raw credentials.

Dedicated tests cover protocol bodies, tenant and personal-account separation, group/role boundaries, stale approvers, credential owner lifecycle, native/Matrix removal ordering, failure rollback, ETags, filter errors, and real synthetic OIDC sign-in after SCIM provisioning/deactivation/reactivation. The real People/SCIM browser proof exercises native reauthentication, one-time credential handling, group rules and offboarding. These are local tests, not certification against a live Entra/Okta tenant, a public deployment or an independent security audit. See [VALIDATION.md](VALIDATION.md).
