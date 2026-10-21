# Customer-managed workforce accounts

Customers choose how new people join their workspace through their own OIDC provider. Open **Integrations → People** as a native workspace owner or administrator. Configure the provider first in **Integrations → Sign-in** using [OIDC setup](OIDC.md).

The default remains **linked**: existing verified personal accounts explicitly link their organization identity using their native password and local MFA. The other modes create or admit an organization-managed account belonging to exactly one workspace. A new managed person does not first register a personal account or create a separate FreeCollab password.

## Choose an admission policy

| API mode | Admission of a previously unknown organization identity |
| --- | --- |
| `linked` | No managed provisioning. Use the existing verified personal-account linking flow. This is the default. |
| `provisioned` | A matching managed principal must already exist, normally created through [SCIM](SCIM.md). |
| `invited` | An already provisioned person may sign in. A new person needs a valid organization invitation bound to this workspace and login transaction. |
| `jit` | A valid accepted provider identity may create a managed workspace member on first sign-in. A valid invitation may instead supply the guest/member role. |

Saving the policy requires the native FreeCollab password and local authenticator/recovery code when enabled. `expected_revision` prevents silently overwriting another administrator's policy change. Policy revisions bind first-sign-in admission decisions; changing the mode is not an offboarding operation for accounts already admitted. Remove membership or deactivate the managed identity to revoke its access.

Before enabling JIT, restrict who may use the OIDC application at the customer's IdP. The UI asks the administrator to acknowledge this admission policy. JIT grants the `member` workspace role; it never trusts provider role/group claims to create owners or installation administrators. Use explicit SCIM group rules for managed access.

Keep a native workspace owner with a usable native sign-in method. Managed accounts cannot become workspace owners, enter installation administration, configure identity providers or create SCIM credentials. A managed workspace administrator can perform permitted ordinary workspace/channel actions, but that role does not create an unrestricted personal-account session.

## Match the directory identity

The **Provisioned identity identifier** selects a signed OIDC claim:

- `sub` is the default and usually the simplest choice.
- `oid` can match a directory object identifier.
- `email` or `preferred_username` can match a customer's explicitly chosen directory identifier.

For SCIM provisioning, set each user's `externalId` to the exact value of that selected claim. Matching is case-sensitive and confined to the selected workspace. When SCIM omits `externalId`, its `userName` becomes the managed external identifier; configure this deliberately rather than assuming it matches an opaque OIDC subject.

On first successful binding, FreeCollab retains the exact workspace, issuer, client ID and provider `sub`. Changing a display name, work email, SCIM correlation value or admission policy does not move that binding to another provider subject. Keep issuer/client identity stable during ordinary secret rotation. Replacing an issuer/client or transferring an established identity requires a planned migration; automatic account/history migration is not provided.

## Personal and organization identities stay separate

A managed principal receives its own internal user ID and a reserved synthetic native email address. Its displayed work email is customer-managed directory metadata, not proof of ownership of a global personal account. The managed account has no usable native password or personal password-reset mailbox.

The same employee email may therefore appear in a personal account and in one or more independent organization accounts. No email lookup merges those accounts, converts the personal account, verifies its email or exposes its other workspaces. Each organization account has its own workspace-bound user and Matrix identity.

If a provider identity is already explicitly linked to a personal account, provisioning must not silently create an alternative route around offboarding. With subject-based matching SCIM rejects an exact conflicting personal link. During OIDC sign-in, an ambiguous personal binding and managed identifier is also rejected. Plan the migration and remove the old workspace sign-in link explicitly; the personal password account and its content remain separate.

## Organization invitations

In invitation-based or JIT mode, choose **Create organization invitation** in People. Invitations grant `member` or `guest`, expire after 24 hours by default, and can be configured for 1–168 hours. The server returns the full link once; its database stores a token hash. Copy it privately for the intended colleague.

The link combines the workspace's opaque SSO routing identifier with a one-use invitation. It still requires authentication at that workspace's configured provider. It is not an email-address verification or an identity-attestation mechanism. Until used or expired, a holder who can authenticate to that IdP may redeem it, so revoke an incorrectly shared link.

The authorization transaction binds the invitation hash and policy revision. Callback checks revalidate expiry, single use, workspace, invitation marker and the creator's current active owner/admin membership. Invitation redemption happens only after verified OIDC claims. These managed invitations cannot be redeemed through the ordinary native workspace-invitation endpoint to bypass the IdP.

People lists available, used, revoked and expired invitations. Unused invitations can be revoked with native password/MFA confirmation. The managed-account roster shows up to 500 accounts; use the paginated SCIM API for larger directories.

## Sign-in and encryption

An admitted organization account receives a normal FreeCollab session with signed workspace scope. Provider revision, current membership, managed-active state and account/session revocation are checked across REST, Socket.IO and Matrix. A missing or inconsistent scope cannot become global account access. The managed identity is denied if it acquires another workspace, an unassigned legacy channel, a personal workspace, or an owner role.

Because its Matrix identity belongs only to this workspace, managed SSO can initialize that identity without asking for a nonexistent personal FreeCollab password. This grants access to the encryption service, not possession of a prior device's message keys. Trusted-device verification, encrypted local keys, cross-signing and the separate history-recovery key still apply. A new device cannot recover history merely by presenting an IdP session.

A personal account linked to customer SSO retains the earlier boundary: its personal Matrix identity can span workspaces, so that scoped session needs a separate native password/MFA encryption unlock lasting at most 30 minutes. This managed-account feature does not weaken that check or merge the two Matrix identities. Secure conversations never fall back to plaintext.

Managed users can revoke their own encryption devices through the encryption settings. This requires explicit confirmation and a FreeCollab SSO session issued within the last five minutes; older sessions receive `requires_recent_organization_signin`. It is a check of the recently issued SSO session, not a guarantee that the IdP requested a fresh password or MFA factor. Configure stronger authentication policy at the customer's IdP when needed.

Device revocation removes the remote device and signs out its linked native session before it can receive new encrypted keys. Revoking the current browser signs that browser out. Already stored ciphertext/plaintext and keys cannot be taken back. Organization sign-in and account reactivation cannot replace a lost encryption recovery key.

## API

These paths include the same-origin `/api` prefix. Configuration endpoints require an unrestricted native workspace owner/admin session. Writes also require `{password, code?}`.

| Operation | Endpoint / additional body |
| --- | --- |
| Read/save policy | `GET` / `PUT /api/workspaces/{id}/identity-policy`; save `mode`, `identity_claim`, optional `expected_revision` |
| List managed people | `GET /api/workspaces/{id}/workforce` |
| Create one-use managed invitation | `POST /api/workspaces/{id}/workforce/invites`; optional `role`, `expires_in_hours` |
| List invitation metadata | `GET /api/workspaces/{id}/workforce/invites` |
| Revoke an invitation | `POST /api/workspaces/{id}/workforce/invites/{invitation_id}/revoke` |
| Revoke an owned managed encryption device | `POST /api/secure/devices/{device_id}/revoke-managed`; `{ "confirmed": true }`, recent scoped managed SSO session |

OIDC authorization still uses code flow, S256 PKCE, signed claim validation, state/nonce, a browser binding and one-use token-free URL handoff. Customer configuration remains independent of the legacy installation-wide OIDC environment option. See [OIDC.md](OIDC.md), [SCIM.md](SCIM.md), [AUTH_SESSIONS.md](AUTH_SESSIONS.md) and [KEY_MANAGEMENT.md](KEY_MANAGEMENT.md).

The implementation has local protocol, API and browser tests with synthetic providers. Their results do not establish a connection to a real customer tenant, public deployment or independent security audit. See [VALIDATION.md](VALIDATION.md) for executed checks and limits.
