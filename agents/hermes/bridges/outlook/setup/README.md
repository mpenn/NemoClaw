# NemoClaw Outlook Bot Provisioning

PowerShell scripts for setting up and tearing down per-user Outlook bot
infrastructure on Microsoft 365 / Exchange Online. Each user gets a dedicated
shared mailbox, Entra app registration, RBAC-scoped Graph API access, and
mail flow rules that pair their bot mailbox to their personal mailbox.

## What gets created per user

Running `Provision-NemoClawUser.ps1 -Username jsmith -Domain contoso.com`
provisions the following resources (assuming the user mailbox's DisplayName
is "Jane Smith"):

| Resource | Name | Purpose |
|---|---|---|
| Shared mailbox | `jsmith-bot@contoso.com` (DisplayName: `Jane Smith Bot`) | Bot's address; receives requests, sends replies |
| App registration | `NemoClaw Bot - Jane Smith` | Identity used by the bridge service to call Graph |
| Client secret | (generated) | Credential for client-credentials OAuth flow |
| Enterprise app | (matches app registration) | Service principal in Entra |
| Exchange service principal | (matches app registration) | Service principal in Exchange Online |
| Management scope | `jsmith Bot Scope` | Filters to the bot mailbox only |
| Management scope | `jsmith User Scope` | Filters to the user mailbox only |
| Role assignment | `jsmith Bot ReadWrite` | Grants `Application Mail.ReadWrite` on bot scope |
| Role assignment | `jsmith Bot Send` | Grants `Application Mail.Send` on bot scope |
| Role assignment | `jsmith User Read` | Grants `Application Mail.Read` on user scope |
| Transport rule | `NemoClaw Outbound - jsmith` | Blocks bot mailbox from sending to anyone except the user |
| Transport rule | `NemoClaw Inbound - jsmith` | Blocks bot mailbox from receiving mail from anyone except the user |

The bot mailbox's DisplayName (`Jane Smith Bot`) is what recipients see in their
inboxes when the bot sends mail. It parallels how the user's own emails appear
(`Jane Smith <jsmith@contoso.com>`) so the bot reads as Matt's automated
counterpart rather than an anonymous service account.

## Security model

The per-user app registration can do exactly three things, all server-enforced
by Exchange Online:

- Read, write, and send mail from the user's bot mailbox
- Read (only) mail from the user's personal mailbox
- Nothing else, on any mailbox in the tenant

If the bot's credentials leak, the blast radius is limited to that one user's
two mailboxes. The app physically cannot send mail as the user, cannot modify
or delete messages in the user's mailbox, and cannot access any other
mailbox. Attempts to do so return HTTP 403 from Graph directly.

Mail flow rules provide a second independent enforcement layer: even if the
bot's code is compromised, the bot mailbox can only exchange messages with
its paired user.

## Prerequisites

### PowerShell modules

```powershell
Install-Module ExchangeOnlineManagement -Scope CurrentUser
Install-Module Microsoft.Graph.Applications -Scope CurrentUser
```

### Caller permissions

The account running these scripts needs:

- **Exchange Online**: member of the `Organization Management` role group
  (required for `New-ManagementScope`, `New-ManagementRoleAssignment`,
  `New-ServicePrincipal`, `New-Mailbox -Shared`, and transport rule cmdlets)
- **Microsoft Graph**: `Application.ReadWrite.All` and `Directory.ReadWrite.All`
  delegated scopes (for creating app registrations and service principals)

Typical roles that satisfy both: Global Administrator, or a combination of
Application Administrator + Exchange Administrator.

### Tenant prerequisites

- `Enable-OrganizationCustomization` must have been run once on the tenant
  (creating any custom management scope requires this; it's permanent and
  irreversible but harmless)
- The user's primary mailbox (`<username>@<domain>`) must already exist.
  These scripts do not provision the user account itself.

## Provisioning a user

### Single user

```powershell
.\Provision-NemoClawUser.ps1 -Username jsmith -Domain contoso.com
```

The script reads the user's DisplayName from their mailbox and uses it to
name the bot (e.g., user "Jane Smith" gets a bot mailbox named "Jane Smith Bot"
and an app registration named "NemoClaw Bot - Jane Smith").

At the end, the script prints the credentials the user's bridge service needs:

```
OUTLOOK_TENANT_ID=...
OUTLOOK_CLIENT_ID=...
OUTLOOK_CLIENT_SECRET=...
OUTLOOK_BOT_MAILBOX=jsmith-bot@contoso.com
OUTLOOK_USER_MAILBOX=jsmith@contoso.com
```

**The client secret is shown only once.** Capture it immediately into your
secret store (Key Vault, 1Password, etc.) and hand it to the user.

### Overriding the real name

If the user mailbox's DisplayName doesn't match what you want displayed
(for example, the mailbox is `jsmith@` but the person goes by "Jane Smith"),
pass `-RealName` explicitly:

```powershell
.\Provision-NemoClawUser.ps1 -Username jsmith -Domain contoso.com `
    -RealName "Jane Smith"
```

This produces a bot mailbox with DisplayName "Jane Smith Bot" and an
app registration named "NemoClaw Bot - Jane Smith".

### Multiple users

```powershell
"jsmith","alice","bob" | ForEach-Object {
    .\Provision-NemoClawUser.ps1 -Username $_ -Domain contoso.com
}
```

### Re-running

The script is idempotent. If it fails partway through, or if you want to
verify an existing setup, re-running is safe — resources that already exist
are skipped. The exception is the client secret, which is **always** created
fresh on each run, so re-running will leave the app with multiple active
secrets. Clean these up manually in Entra if needed.

## Deprovisioning a user

### Dry run (default — recommended first step)

```powershell
.\Deprovision-NemoClawUser.ps1 -Username jsmith -Domain contoso.com
```

Reports what would be deleted without touching anything. **Always dry-run
first** to confirm you're targeting the right user.

### Actually deprovision

```powershell
.\Deprovision-NemoClawUser.ps1 -Username jsmith -Domain contoso.com -Confirm
```

Removes everything in reverse order of creation:

1. Mail flow rules (stops traffic gating first)
2. RBAC role assignments
3. Management scopes
4. Exchange service principal
5. Enterprise application + app registration (removes all secrets)
6. Shared bot mailbox **(all bot mailbox messages are lost)**

### Keep the mailbox for archival

```powershell
.\Deprovision-NemoClawUser.ps1 -Username jsmith -Domain contoso.com `
    -Confirm -KeepMailbox
```

Removes all access infrastructure but preserves the bot mailbox and its
message history. Useful if the user is leaving but audit/legal requires
retaining the conversation log, or if you're rotating credentials by
deprovision + reprovision and want to keep the mailbox intact.

### When the user mailbox is already gone

The app registration's name embeds the user's real name (e.g.
`NemoClaw Bot - Jane Smith`). Normally the script looks this up from the
user's mailbox, but if the mailbox has already been deleted, it can't.
The script tries to recover by matching role assignments back to their
service principal, which works in most cases. If that fails too, pass
`-RealName` explicitly:

```powershell
.\Deprovision-NemoClawUser.ps1 -Username jsmith -Domain contoso.com `
    -RealName "Jane Smith" -Confirm
```

This is mostly a concern for cleanup of stale provisioning — in normal
flow, you'd deprovision before deleting the user account, so the mailbox
is still available for name lookup.

## Verification

The provisioning script runs `Test-ServicePrincipalAuthorization` at the end
and prints the results. You should see:

**On the bot mailbox:**
```
RoleName                       InScope
--------                       -------
Application Mail.ReadWrite     True
Application Mail.Send          True
Application Mail.Read          False
```

**On the user mailbox:**
```
RoleName                       InScope
--------                       -------
Application Mail.ReadWrite     False
Application Mail.Send          False
Application Mail.Read          True
```

If `Mail.Send` is `True` on the user mailbox, something is misconfigured and
the app can send email as the user — stop and investigate before handing
over credentials.

You can also run this check manually any time:

```powershell
Test-ServicePrincipalAuthorization -Identity <ClientId> `
    -Resource jsmith@contoso.com
```

RBAC changes can take 30 minutes to 2 hours to propagate, so a freshly
provisioned app may show stale results briefly.

## Bridge service configuration

Once provisioned, the user's `outlook-bridge` service needs these environment
variables:

| Variable | Value |
|---|---|
| `OUTLOOK_TENANT_ID` | Tenant ID (from script output) |
| `OUTLOOK_CLIENT_ID` | App registration client ID (from script output) |
| `OUTLOOK_CLIENT_SECRET` | Client secret (from script output) |
| `OUTLOOK_BOT_MAILBOX` | `<username>-bot@<domain>` |
| `OUTLOOK_USER_MAILBOX` | `<username>@<domain>` |

The bridge uses these to authenticate via OAuth client credentials flow
against `https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token` with
scope `https://graph.microsoft.com/.default`, then makes Graph API calls
against the bot mailbox (for polling, replying, marking read) and the user
mailbox (for read-only search).

## Operational notes

### Secret rotation

Client secrets created by the provisioning script expire in 24 months. Before
expiration, rotate with:

```powershell
# Add a fresh secret
$app = Get-MgApplication -Filter "displayName eq 'NemoClaw Bot - jsmith'"
Add-MgApplicationPassword -ApplicationId $app.Id `
    -PasswordCredential @{
        displayName = "Rotation $(Get-Date -Format yyyy-MM-dd)"
        endDateTime = (Get-Date).AddMonths(24)
    }

# Deploy the new secret to the user's bridge service, then delete the old one
```

### Auditing

App-specific activity shows up in:

- **Entra sign-in logs** under the app's service principal (one entry per
  token request)
- **Unified audit log** for mail operations (send, delete, etc.)
- **Message trace** in Exchange admin center for individual email deliveries

Because apps are per-user, audit trails are cleanly separated by user.

### Troubleshooting 403 errors

If the bridge service starts returning 403s after a previously-working setup:

1. Run `Test-ServicePrincipalAuthorization` to confirm the role assignments
   are still in place and `InScope` for the expected mailboxes
2. Check that the client secret hasn't expired
3. Check that the user mailbox and bot mailbox still exist and are still
   members of the expected scopes
4. Remember RBAC changes can take up to 2 hours to propagate — if you just
   made a change, wait before concluding it didn't work

### Emergency disable

To instantly disable a user's bot without full deprovisioning:

```powershell
# Disables the app registration — all token requests start failing
Update-MgApplication -ApplicationId $app.Id -IsDisabled:$true
```

Or remove the client secret to force the bridge service to fail at the
token endpoint.

## Naming conventions

These scripts use a specific naming scheme — adjust if your org has different
conventions. Most names use `<username>` (the mailbox local part), but the
bot mailbox DisplayName and the app registration use `<RealName>` (the user's
actual display name, read from their mailbox) so the bot reads naturally in
email clients.

| Resource | Format | Example |
|---|---|---|
| Bot mailbox (SMTP) | `<username>-bot@<domain>` | `jsmith-bot@contoso.com` |
| Bot mailbox (DisplayName) | `<RealName> Bot` | `Jane Smith Bot` |
| App registration | `NemoClaw Bot - <RealName>` | `NemoClaw Bot - Jane Smith` |
| Management scopes | `<username> Bot Scope`, `<username> User Scope` | `jsmith Bot Scope` |
| Role assignments | `<username> Bot ReadWrite`, `<username> Bot Send`, `<username> User Read` | `jsmith Bot ReadWrite` |
| Transport rules | `NemoClaw Outbound - <username>`, `NemoClaw Inbound - <username>` | `NemoClaw Outbound - jsmith` |

Why the mix? Usernames are stable and URL-safe, so they anchor the internal
resources (scopes, rules, role assignments) that are referenced by other
scripts or logs. RealNames look better in email headers and Entra UI, so
they're used for anything the user or their recipients will see.

The deprovisioning script relies on these exact names to find resources.
If you rename anything manually, update the deprovision script to match
or clean up the renamed resource by hand.

## Files

- `Provision-NemoClawUser.ps1` — creates per-user bot infrastructure
- `Deprovision-NemoClawUser.ps1` — removes per-user bot infrastructure
- `README.md` — this file
